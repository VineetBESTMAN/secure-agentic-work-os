from app.models.schemas import ActionProposal, AgentPlanResponse, UserContext


class AgentService:
    def build_plan(self, prompt: str, user: UserContext) -> AgentPlanResponse:
        normalized = prompt.lower()
        actions = [
            ActionProposal(
                action_id="act_search_documents",
                action_type="search_documents",
                description="Search workspace knowledge for urgent or client-related work.",
                requires_approval=False,
                scope="documents:read",
            ),
            ActionProposal(
                action_id="act_create_task",
                action_type="create_task",
                description="Create follow-up tasks from the relevant messages.",
                requires_approval=False,
                scope="tasks:write",
            ),
        ]
        if "reply" in normalized or "send" in normalized:
            actions.append(
                ActionProposal(
                    action_id="act_send_email",
                    action_type="send_email",
                    description="Draft and optionally send a response email.",
                    requires_approval=True,
                    scope="email:send",
                )
            )

        return AgentPlanResponse(
            summary="Prepared an agent plan with review checkpoints before risky actions.",
            actions=actions,
        )


agent_service = AgentService()
