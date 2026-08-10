from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify

from tasker.models import AIConversation, AIMessage, PromptTemplate

from .exceptions import AIPermissionError


class ConversationManager:
    HISTORY_LIMIT = 40

    @transaction.atomic
    def create(self, *, user, title="", agent_type="assistant", metadata=None):
        return AIConversation.objects.create(
            owner=user,
            title=title[:255],
            agent_type=slugify(str(agent_type))[:80] or "assistant",
            context_metadata=metadata or {},
            created_by=user,
            updated_by=user,
        )

    def get_for_user(self, *, user, conversation_id):
        try:
            return AIConversation.objects.get(owner=user, pk=conversation_id)
        except AIConversation.DoesNotExist as exc:
            raise AIPermissionError("The conversation is unavailable.") from exc

    @transaction.atomic
    def append(
        self,
        *,
        conversation,
        role,
        content,
        user,
        status=AIMessage.Status.COMPLETED,
        prompt_template: PromptTemplate | None = None,
        metadata=None,
    ):
        conversation = AIConversation.objects.select_for_update().get(pk=conversation.pk)
        last_sequence = conversation.messages.aggregate(last=Max("sequence"))["last"] or 0
        message = AIMessage.objects.create(
            conversation=conversation,
            role=role,
            status=status,
            sequence=last_sequence + 1,
            content=content,
            prompt_template=prompt_template,
            message_metadata=metadata or {},
            created_by=user,
            updated_by=user,
        )
        conversation.last_message_at = timezone.now()
        conversation.updated_by = user
        conversation.save(update_fields=["last_message_at", "updated_by", "updated_at"])
        return message

    def provider_history(self, *, conversation):
        messages = list(
            conversation.messages.filter(
                status=AIMessage.Status.COMPLETED,
                role__in=(AIMessage.Role.USER, AIMessage.Role.ASSISTANT),
            ).order_by("-sequence")[: self.HISTORY_LIMIT]
        )
        messages.reverse()
        return [{"role": message.role.lower(), "content": message.content} for message in messages]
