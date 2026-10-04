"""Wrapping a gRPC method handler without changing what kind of handler it is.

A server interceptor that wants to run something around a servicer replaces
its handler, and two properties of the original have to survive that:

* its kind (unary or streaming on each side), which decides how grpc reads
  requests and writes responses;
* its execution model. grpc.aio runs a coroutine or an async generator on the
  event loop, and a plain function or generator on its thread pool with a
  synchronous context. Wrapping a plain servicer in a coroutine would run
  blocking code on the loop; awaiting it would fail outright.

:func:`wrap` keeps both, for every kind and both models, so each interceptor
states only what it does around the call.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import grpc

#: Called with the call's context as the servicer starts; returns what to call
#: when it ends -- always, with the exception it ended on or None -- or None.
Enter = Callable[[Any], Callable[[BaseException | None], None] | None]

#: Asked about an exception the servicer raised: the status to end the call
#: with, or None to let it propagate unchanged.
OnError = Callable[[BaseException], tuple[grpc.StatusCode, str] | None]


class _SyncContext:
    """The synchronous context grpc.aio gives a thread-pool servicer, able to
    say which status was set on it.

    That context has ``set_code`` but no ``code``, so a layer further out
    could not learn how the call ended. Every wrapper layer hands the servicer
    one of these over the context it was given, and each records the code as
    it passes through. Everything else is the underlying context's.
    """

    def __init__(self, context: Any) -> None:
        self._context = context
        self._code: grpc.StatusCode | None = None

    def set_code(self, code: grpc.StatusCode) -> None:
        self._code = code
        self._context.set_code(code)

    def code(self) -> grpc.StatusCode | None:
        return self._code

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)


def factory(handler: grpc.RpcMethodHandler) -> Any:
    """The constructor of *handler*'s kind."""
    if handler.request_streaming and handler.response_streaming:
        return grpc.stream_stream_rpc_method_handler
    if handler.request_streaming:
        return grpc.stream_unary_rpc_method_handler
    if handler.response_streaming:
        return grpc.unary_stream_rpc_method_handler
    return grpc.unary_unary_rpc_method_handler


def behaviour(handler: grpc.RpcMethodHandler) -> Any:
    """The servicer function *handler* calls, whatever its kind."""
    return (
        handler.unary_unary
        or handler.unary_stream
        or handler.stream_unary
        or handler.stream_stream
    )


def rebuild(
    handler: grpc.RpcMethodHandler, new_behaviour: Any
) -> grpc.RpcMethodHandler:
    """A handler of *handler*'s kind and codec around *new_behaviour*."""
    return factory(handler)(
        new_behaviour,
        request_deserializer=handler.request_deserializer,
        response_serializer=handler.response_serializer,
    )


def _end_sync(context: Any, status: tuple[grpc.StatusCode, str]) -> None:
    """End a plain servicer's call with *status*.

    Not with ``abort``: from a plain streaming servicer it leaves the call
    hanging until its deadline in about a third of calls (grpc 1.81, measured
    without any interceptor). Setting the code and details and returning
    delivers both every time; raising afterwards would replace the details
    with "Unexpected <exception>".
    """
    context.set_code(status[0])
    context.set_details(status[1])


def _is_async(inner: Any) -> bool:
    return inspect.iscoroutinefunction(inner) or inspect.isasyncgenfunction(inner)


def wrap(
    handler: grpc.RpcMethodHandler,
    *,
    enter: Enter | None = None,
    on_error: OnError | None = None,
) -> grpc.RpcMethodHandler:
    """*handler*, with *enter* around each call and *on_error* on a failure.

    An abort the servicer itself raised is never re-judged by *on_error*.
    """
    inner = behaviour(handler)

    def _start(context: Any) -> Callable[[BaseException | None], None] | None:
        return enter(context) if enter is not None else None

    def _status(error: BaseException) -> tuple[grpc.StatusCode, str] | None:
        if on_error is None or isinstance(error, grpc.aio.AbortError):
            return None
        return on_error(error)

    wrapped: Any
    if _is_async(inner) and handler.response_streaming:

        async def _async_stream(request: Any, context: Any) -> AsyncIterator[Any]:
            end = _start(context)
            failure: BaseException | None = None
            try:
                result = inner(request, context)
                if inspect.isasyncgen(result):
                    async for item in result:
                        yield item
                else:
                    # A coroutine that writes with context.write.
                    await result
            except BaseException as error:
                failure = error
                status = _status(error)
                if status is None:
                    raise
                # Raises grpc's AbortError, which is how the call ends.
                await context.abort(*status)
                raise  # pragma: no cover - abort raises
            finally:
                if end is not None:
                    end(failure)

        wrapped = _async_stream
    elif _is_async(inner):

        async def _async_single(request: Any, context: Any) -> Any:
            end = _start(context)
            failure: BaseException | None = None
            try:
                return await inner(request, context)
            except BaseException as error:
                failure = error
                status = _status(error)
                if status is None:
                    raise
                # Raises grpc's AbortError, which is how the call ends.
                await context.abort(*status)
                raise  # pragma: no cover - abort raises
            finally:
                if end is not None:
                    end(failure)

        wrapped = _async_single
    elif handler.response_streaming:

        def _sync_stream(request: Any, given: Any) -> Iterator[Any]:
            context = _SyncContext(given)
            end = _start(context)
            failure: BaseException | None = None
            try:
                yield from inner(request, context)
            except BaseException as error:
                status = _status(error)
                if status is None:
                    failure = error
                    raise
                _end_sync(context, status)
            finally:
                if end is not None:
                    end(failure)

        wrapped = _sync_stream
    else:

        def _sync_single(request: Any, given: Any) -> Any:
            context = _SyncContext(given)
            end = _start(context)
            failure: BaseException | None = None
            try:
                return inner(request, context)
            except BaseException as error:
                status = _status(error)
                if status is None:
                    failure = error
                    raise
                _end_sync(context, status)
                return None
            finally:
                if end is not None:
                    end(failure)

        wrapped = _sync_single
    return rebuild(handler, wrapped)


__all__ = [
    "Enter",
    "OnError",
    "behaviour",
    "factory",
    "rebuild",
    "wrap",
]
