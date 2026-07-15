from uuid import uuid4

from app.core.database import decode_json, encode_json, get_connection, is_postgres_database
from app.models.schemas import PolicyCreateRequest, PolicyRecord, UserContext


DEFAULT_POLICIES = [
    PolicyCreateRequest(
        name="Employees cannot access restricted documents",
        description="Blocks non-admin users from accessing restricted classifications.",
        rule_type="document_access",
        effect="block",
        conditions={"roles": ["employee", "manager"], "classification": "restricted"},
    ),
    PolicyCreateRequest(
        name="External email requires approval",
        description="Requires human approval before sending email outside the workspace.",
        rule_type="tool_approval",
        effect="approval_required",
        conditions={"tool_name": "send_email"},
    ),
    PolicyCreateRequest(
        name="Unsafe retrieved content cannot trigger tools",
        description="Blocks tool execution when prompt-injection patterns are detected.",
        rule_type="prompt_safety",
        effect="block",
        conditions={"unsafe": True},
    ),
]


class PolicyService:
    def seed_defaults(self, organization_id: str = "org_default") -> None:
        existing_names = {policy.name for policy in self.list_policies(organization_id)}
        for policy in DEFAULT_POLICIES:
            if policy.name not in existing_names:
                self.create_policy(policy, organization_id)

    def list_policies(self, organization_id: str = "org_default") -> list[PolicyRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM policies WHERE organization_id = ? ORDER BY created_at DESC",
                (organization_id,),
            ).fetchall()
        return [self._row_to_policy(row) for row in rows]

    def create_policy(
        self, payload: PolicyCreateRequest, organization_id: str = "org_default"
    ) -> PolicyRecord:
        policy = PolicyRecord(
            policy_id=f"pol_{uuid4().hex}",
            name=payload.name,
            description=payload.description,
            rule_type=payload.rule_type,
            effect=payload.effect,
            conditions=payload.conditions,
            enabled=payload.enabled,
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO policies (
                    policy_id, name, description, rule_type, effect, conditions_json,
                    enabled, organization_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy.policy_id,
                    policy.name,
                    policy.description,
                    policy.rule_type,
                    policy.effect,
                    encode_json(policy.conditions),
                    policy.enabled,
                    organization_id,
                ),
            )
        return policy

    def document_access_allowed(
        self,
        user: UserContext | None,
        role: str,
        classification: str,
        organization_id: str = "org_default",
    ) -> bool:
        organization_id = user.organization_id if user else organization_id
        for policy in self._enabled_by_type("document_access", organization_id):
            conditions = policy.conditions
            roles = conditions.get("roles")
            policy_classification = conditions.get("classification")
            role_matches = not roles or role in roles
            classification_matches = (
                not policy_classification or policy_classification == classification
            )
            if role_matches and classification_matches and policy.effect == "block":
                return False
        return True

    def tool_requires_approval(
        self, tool_name: str, organization_id: str = "org_default"
    ) -> bool:
        for policy in self._enabled_by_type("tool_approval", organization_id):
            if policy.conditions.get("tool_name") == tool_name:
                return policy.effect == "approval_required"
        return False

    def unsafe_content_blocks_tools(
        self, unsafe: bool, organization_id: str = "org_default"
    ) -> bool:
        for policy in self._enabled_by_type("prompt_safety", organization_id):
            if policy.conditions.get("unsafe") is unsafe and policy.effect == "block":
                return True
        return False

    def _enabled_by_type(
        self, rule_type: str, organization_id: str
    ) -> list[PolicyRecord]:
        return [
            policy
            for policy in self.list_policies(organization_id)
            if policy.enabled and policy.rule_type == rule_type
        ]

    def _row_to_policy(self, row) -> PolicyRecord:
        created_at = row["created_at"]
        return PolicyRecord(
            policy_id=row["policy_id"],
            name=row["name"],
            description=row["description"],
            rule_type=row["rule_type"],
            effect=row["effect"],
            conditions=decode_json(row["conditions_json"], {}),
            enabled=bool(row["enabled"]),
            created_at=str(created_at) if created_at is not None else None,
        )


policy_service = PolicyService()
