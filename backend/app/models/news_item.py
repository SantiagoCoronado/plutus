from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NewsItem(Base):
    """Deduped news headlines. Uniqueness = md5(url) expression index (created in the
    migration — avoids b-tree limits on long URLs); upserts merge `tickers` so one
    article shared by several symbols accumulates them."""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text)
    headline: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    tickers: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'")
    )
    sentiment: Mapped[float | None]  # Finnhub free tier has none; nullable for later
