from datetime import datetime, time, timezone
import math

from sqlalchemy import func
from sqlalchemy.orm import Session

from App.models.ai_usage import AIUsage
from App.models.user import User


class UsageLimitExceeded(Exception):
    def __init__(self, limit: str):
        self.limit = limit
        super().__init__(f"Daily AI {limit} limit exceeded.")


class AIUsageService:
    """Persist request usage and enforce configurable daily user limits."""

    def __init__(
        self,
        db: Session,
        request_limit: int,
        token_limit: int,
        output_reservation: int = 512,
    ):
        if min(request_limit, token_limit, output_reservation) < 0:
            raise ValueError("AI usage limits cannot be negative.")
        self.db = db
        self.request_limit = request_limit
        self.token_limit = token_limit
        self.output_reservation = output_reservation

    @staticmethod
    def estimate_tokens(value: str) -> int:
        if not value:
            return 0
        return max(1, math.ceil(len(value.encode("utf-8")) / 4))

    @staticmethod
    def _day_start() -> datetime:
        now = datetime.now(timezone.utc)
        return datetime.combine(now.date(), time.min)

    def start_request(
        self,
        user_id: int,
        project_id: int | None,
        conversation_id: int | None,
        provider: str,
        model: str,
        estimated_input_tokens: int,
    ) -> int:
        self.db.query(User).filter(User.id == user_id).with_for_update().first()
        day_start = self._day_start()
        usage_query = self.db.query(AIUsage).filter(
            AIUsage.user_id == user_id,
            AIUsage.requested_at >= day_start,
        )
        request_count = usage_query.with_entities(func.count(AIUsage.id)).scalar() or 0
        if self.request_limit and request_count >= self.request_limit:
            raise UsageLimitExceeded("request")

        tokens_used = (
            usage_query.with_entities(
                func.coalesce(
                    func.sum(AIUsage.total_tokens + AIUsage.reserved_tokens),
                    0,
                )
            ).scalar()
            or 0
        )
        reservation = estimated_input_tokens + self.output_reservation
        if self.token_limit and tokens_used + reservation > self.token_limit:
            raise UsageLimitExceeded("token")

        record = AIUsage(
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            provider=provider,
            model=model,
            requested_at=datetime.now(timezone.utc).replace(tzinfo=None),
            input_tokens=estimated_input_tokens,
            output_tokens=0,
            total_tokens=0,
            reserved_tokens=reservation,
            token_count_source="estimated",
            request_status="pending",
            error_status=None,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record.id

    def finish_request(
        self,
        usage_id: int,
        input_tokens: int,
        output_tokens: int,
        request_status: str,
        error_status: str | None = None,
    ) -> None:
        record = (
            self.db.query(AIUsage)
            .filter(AIUsage.id == usage_id)
            .first()
        )
        if record is None:
            raise LookupError("AI usage record disappeared before completion.")
        record.input_tokens = max(0, input_tokens)
        record.output_tokens = max(0, output_tokens)
        record.total_tokens = record.input_tokens + record.output_tokens
        record.reserved_tokens = 0
        record.request_status = request_status
        record.error_status = error_status
        self.db.commit()