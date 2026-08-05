"""HTTP endpoints for the purpose vocabulary and its templates. Read-only.

Templates are data in the repository, validated at startup
(`app.services.templates`), so there is nothing to write here. The endpoint
exists so the planning UI pre-fills its criteria editor from the same
templates the server derives defaults from, rather than from a second copy
that would drift.
"""

from fastapi import APIRouter

from app.api.schemas.purposes import PurposeTemplateRead, PurposeTemplatesRead
from app.domain.criteria import criteria_to_json
from app.domain.purpose import Purpose
from app.domain.templates import PurposeTemplate, sorted_templates
from app.services.templates import purpose_templates

router = APIRouter(prefix="/purposes", tags=["purposes"])


def _to_read(template: PurposeTemplate) -> PurposeTemplateRead:
    """Project a template onto its response shape."""
    return PurposeTemplateRead.model_validate(
        {
            "purpose": template.purpose,
            "discipline": template.discipline,
            "axes": list(template.axes),
            "default_criteria": criteria_to_json(template.default_criteria),
        }
    )


@router.get("")
async def list_purposes() -> PurposeTemplatesRead:
    """List every purpose with its scoring axes and default success criteria."""
    return PurposeTemplatesRead(
        items=[_to_read(template) for template in sorted_templates(purpose_templates())]
    )


@router.get("/{purpose}")
async def get_purpose(purpose: Purpose) -> PurposeTemplateRead:
    """Get one purpose's template.

    No 404 is declared because none is reachable: the path parameter is the
    purpose enum, so an unknown value is a 422 from the parser, and every
    known value has a template or the application would not have booted.
    """
    return _to_read(purpose_templates()[purpose])
