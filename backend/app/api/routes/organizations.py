from fastapi import APIRouter, Depends, HTTPException, status

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import (
    InvitationCreateRequest,
    InvitationRecord,
    MembershipUpdateRequest,
    OrganizationCreateRequest,
    OrganizationMemberRecord,
    OrganizationSummary,
    OIDCProviderCreateRequest,
    OIDCProviderRecord,
)
from app.services.audit import audit_service
from app.services.users import user_service
from app.services.oidc import oidc_service
from app.services.observability import observability_service
from app.services.policies import policy_service
from app.core.config import get_settings

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("", response_model=list[OrganizationSummary])
def list_organizations(user=Depends(get_current_user)) -> list[OrganizationSummary]:
    return user_service.list_organizations(user.user_id)


@router.post("", response_model=OrganizationSummary, status_code=status.HTTP_201_CREATED)
def create_organization(
    payload: OrganizationCreateRequest, user=Depends(get_current_user)
) -> OrganizationSummary:
    try:
        organization = user_service.create_organization(payload.name, payload.slug, user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    policy_service.seed_defaults(organization.organization_id)
    observability_service.seed_defaults(
        get_settings().default_daily_cost_limit_usd, organization.organization_id
    )
    audit_service.record(
        actor_id=user.user_id,
        event_type="organizations.create",
        detail={"created_organization_id": organization.organization_id},
        organization_id=user.organization_id,
    )
    return organization


@router.get("/current/members", response_model=list[OrganizationMemberRecord])
def list_members(user=Depends(get_current_user)) -> list[OrganizationMemberRecord]:
    require_roles(user.role, {"admin", "manager"})
    return user_service.list_members(user.organization_id)


@router.patch(
    "/current/members/{membership_id}", response_model=OrganizationMemberRecord
)
def update_member(
    membership_id: str,
    payload: MembershipUpdateRequest,
    user=Depends(get_current_user),
) -> OrganizationMemberRecord:
    require_roles(user.role, {"admin"})
    try:
        member = user_service.update_membership(
            user.organization_id, membership_id, payload, user
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    audit_service.record(
        actor_id=user.user_id,
        event_type="organizations.membership_update",
        detail={"membership_id": membership_id},
        organization_id=user.organization_id,
    )
    return member


@router.get("/current/invitations", response_model=list[InvitationRecord])
def list_invitations(user=Depends(get_current_user)) -> list[InvitationRecord]:
    require_roles(user.role, {"admin", "manager"})
    return user_service.list_invitations(user.organization_id)


@router.post(
    "/current/invitations",
    response_model=InvitationRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    payload: InvitationCreateRequest, user=Depends(get_current_user)
) -> InvitationRecord:
    require_roles(user.role, {"admin"})
    try:
        invitation = user_service.create_invitation(
            user.organization_id, payload, user.user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_service.record(
        actor_id=user.user_id,
        event_type="organizations.invitation_create",
        detail={"invitation_id": invitation.invitation_id, "email": invitation.email},
        organization_id=user.organization_id,
    )
    return invitation


@router.get("/current/oidc-providers", response_model=list[OIDCProviderRecord])
def list_oidc_providers(user=Depends(get_current_user)) -> list[OIDCProviderRecord]:
    require_roles(user.role, {"admin"})
    return oidc_service.list_providers(user.organization_id)


@router.post(
    "/current/oidc-providers",
    response_model=OIDCProviderRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_oidc_provider(
    payload: OIDCProviderCreateRequest, user=Depends(get_current_user)
) -> OIDCProviderRecord:
    require_roles(user.role, {"admin"})
    try:
        provider = oidc_service.create_provider(user.organization_id, user.user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    audit_service.record(
        actor_id=user.user_id,
        event_type="organizations.oidc_provider_create",
        detail={"provider_id": provider.provider_id, "issuer": provider.issuer_url},
        organization_id=user.organization_id,
    )
    return provider
