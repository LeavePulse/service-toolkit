"""Integration helpers for the internal `awesome-errors` library."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping, TypeAlias

if TYPE_CHECKING:
    ErrorResponseFormat: TypeAlias = Any
    ErrorTranslator: TypeAlias = Any

    def apply_litestar_openapi_problem_details(*args: Any, **kwargs: Any) -> None: ...
    def create_litestar_exception_handlers(*args: Any, **kwargs: Any) -> Any: ...

else:
    try:  # pragma: no cover - optional dependency
        from awesome_errors import (
            ErrorResponseFormat,
            ErrorTranslator,
            apply_litestar_openapi_problem_details,
            create_litestar_exception_handlers,
        )
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name == "awesome_errors":
            raise ModuleNotFoundError(
                "awesome-errors helpers require the optional 'errors' extra. "
                "Install with 'pip install service-toolkit[errors]' or add "
                "'awesome-errors' to your service dependencies."
            ) from exc
        raise


def build_standard_exception_handlers(
    *,
    service_name: str,
    translator: ErrorTranslator,
    debug: bool,
    response_format: ErrorResponseFormat = ErrorResponseFormat.RFC7807,
    problem_type_resolver: Callable[[Any], str] | None = None,
    problem_extension_builder: Callable[[Any], Mapping[str, Any]] | None = None,
    **kwargs: Any,
) -> Any:
    """Build Litestar exception handlers using a consistent RFC7807 format."""

    if problem_type_resolver is None:

        def default_problem_type_resolver(error: Any) -> str:
            return f"urn:{service_name}:error:{error.code.value.lower()}"

        problem_type_resolver = default_problem_type_resolver

    if problem_extension_builder is None:

        def default_problem_extension_builder(_error: Any) -> Mapping[str, Any]:
            return {"service": service_name}

        problem_extension_builder = default_problem_extension_builder

    return create_litestar_exception_handlers(
        translator=translator,
        debug=debug,
        response_format=response_format,
        problem_type_resolver=problem_type_resolver,
        problem_extension_builder=problem_extension_builder,
        **kwargs,
    )


def build_error_translator_with_defaults(
    *,
    service_name: str,
    custom_translations: Mapping[str, Mapping[str, str]] | None = None,
    default_locale: str = "en",
) -> ErrorTranslator:
    """Create ``ErrorTranslator`` with common defaults and service-level overrides.

    This keeps service ``main.py`` files concise and avoids repeated setup code.
    """

    translator = ErrorTranslator(default_locale=default_locale)

    translator.add_translations(
        "en",
        {
            "OAUTH_PROVIDER_UNKNOWN": "Unknown OAuth provider",
            "SESSION_EXPIRED": "Session is no longer valid",
            "RESOURCE_NOT_FOUND": "Resource not found",
            "INTERNAL_ERROR": f"{service_name} internal error",
        },
        persist=False,
    )
    translator.add_translations(
        "uk",
        {
            "OAUTH_PROVIDER_UNKNOWN": "Невідомий OAuth-провайдер",
            "SESSION_EXPIRED": "Сесія більше не дійсна",
            "RESOURCE_NOT_FOUND": "Ресурс не знайдено",
        },
        persist=False,
    )

    if custom_translations:
        for locale, locale_translations in custom_translations.items():
            if not locale_translations:
                continue
            translator.add_translations(
                locale, dict(locale_translations), persist=False
            )

    return translator


def apply_problem_details(app: Any, *, service_name: str) -> None:
    """Apply awesome-errors OpenAPI problem details integration to a Litestar app."""

    apply_litestar_openapi_problem_details(app, service_name=service_name)


__all__ = [
    "ErrorResponseFormat",
    "ErrorTranslator",
    "apply_problem_details",
    "build_error_translator_with_defaults",
    "build_standard_exception_handlers",
]
