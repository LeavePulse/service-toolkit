"""Litestar decorator for projection-aware handlers."""

from __future__ import annotations

from functools import wraps
from inspect import Parameter as SignatureParameter
from inspect import isawaitable, signature
from types import SimpleNamespace
from typing import Any, Callable

from litestar.params import Parameter

from .projection import ProjectionSpec


def with_projection(
    *,
    allowed_paths: tuple[str, ...] | list[str] | set[str] | frozenset[str],
    fields_description: str | None = None,
    include_description: str | None = None,
    exclude_description: str | None = None,
):
    """Add shared projection query params and attach the parsed spec to request."""

    allowed_text = ", ".join(sorted(str(path) for path in allowed_paths))
    resolved_fields_description = (
        fields_description
        or f"Optional projection fields: {allowed_text}."
    )
    resolved_include_description = (
        include_description
        or f"Optional related blocks to include: {allowed_text}."
    )
    resolved_exclude_description = (
        exclude_description
        or f"Optional blocks to exclude from the default response: {allowed_text}."
    )

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        original_signature = signature(fn)
        if "request" not in original_signature.parameters:
            msg = "Projection-aware handlers must declare a 'request' parameter."
            raise TypeError(msg)

        conflicting_names = {
            name for name in ("fields", "include", "exclude") if name in original_signature.parameters
        }
        if conflicting_names:
            joined = ", ".join(sorted(conflicting_names))
            msg = f"Handler already declares projection params: {joined}."
            raise TypeError(msg)

        new_parameters = list(original_signature.parameters.values())
        new_parameters.extend(
            [
                SignatureParameter(
                    name="fields",
                    kind=SignatureParameter.KEYWORD_ONLY,
                    annotation=str | None,
                    default=Parameter(
                        default=None,
                        description=resolved_fields_description,
                    ),
                ),
                SignatureParameter(
                    name="include",
                    kind=SignatureParameter.KEYWORD_ONLY,
                    annotation=str | None,
                    default=Parameter(
                        default=None,
                        description=resolved_include_description,
                    ),
                ),
                SignatureParameter(
                    name="exclude",
                    kind=SignatureParameter.KEYWORD_ONLY,
                    annotation=str | None,
                    default=Parameter(
                        default=None,
                        description=resolved_exclude_description,
                    ),
                ),
            ]
        )
        wrapped_signature = original_signature.replace(parameters=new_parameters)

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = wrapped_signature.bind_partial(*args, **kwargs)
            request = bound.arguments.get("request")
            if request is None:
                msg = "Projection-aware handlers require a request instance."
                raise TypeError(msg)

            fields = bound.arguments.pop("fields", None)
            include = bound.arguments.pop("include", None)
            exclude = bound.arguments.pop("exclude", None)
            kwargs.pop("fields", None)
            kwargs.pop("include", None)
            kwargs.pop("exclude", None)

            projection = ProjectionSpec.from_query_params(
                fields=fields,
                include=include,
                exclude=exclude,
            ).restrict_to(allowed_paths)

            try:
                setattr(request, "projection", projection)
            except AttributeError:
                pass

            state = getattr(request, "state", None)
            if state is None:
                state = SimpleNamespace()
                try:
                    setattr(request, "state", state)
                except AttributeError:
                    state = None
            if state is not None:
                setattr(state, "projection", projection)

            result = fn(*args, **kwargs)
            if isawaitable(result):
                return await result
            return result

        wrapper.__signature__ = wrapped_signature
        wrapper.__annotations__ = {
            **getattr(fn, "__annotations__", {}),
            "fields": str | None,
            "include": str | None,
            "exclude": str | None,
        }
        return wrapper

    return decorator


__all__ = ["with_projection"]
