from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from .database import Base

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sub: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    reservations: Mapped[list["Reservation"]] = relationship("Reservation", back_populates="user")

class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    color: Mapped[str] = mapped_column(String, default="#3788d8")
    reservations: Mapped[list["Reservation"]] = relationship("Reservation", back_populates="resource")

class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String)
    start_utc: Mapped[datetime] = mapped_column(DateTime)
    end_utc: Mapped[datetime] = mapped_column(DateTime)
    description: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="reservations")
    resource: Mapped["Resource"] = relationship("Resource", back_populates="reservations")


# --- New multi-home models (kept separate to avoid altering legacy tables) ---

class Home(Base):
    __tablename__ = "homes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    owner: Mapped["User"] = relationship("User")
    members: Mapped[list["HomeMember"]] = relationship("HomeMember", back_populates="home", cascade="all, delete-orphan")
    resources: Mapped[list["HomeResource"]] = relationship("HomeResource", back_populates="home", cascade="all, delete-orphan")


class HomeMember(Base):
    __tablename__ = "home_members"
    __table_args__ = (
        UniqueConstraint("home_id", "user_id", name="uq_home_user"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    home: Mapped["Home"] = relationship("Home", back_populates="members")
    user: Mapped["User"] = relationship("User")


class HomeResource(Base):
    __tablename__ = "home_resources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    name: Mapped[str] = mapped_column(String)
    color: Mapped[str] = mapped_column(String, default="#3788d8")

    home: Mapped["Home"] = relationship("Home", back_populates="resources")
    reservations: Mapped[list["HomeReservation"]] = relationship("HomeReservation", back_populates="resource", cascade="all, delete-orphan")


class HomeReservation(Base):
    __tablename__ = "home_reservations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    resource_id: Mapped[int] = mapped_column(ForeignKey("home_resources.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String)
    start_utc: Mapped[datetime] = mapped_column(DateTime)  # naive UTC
    end_utc: Mapped[datetime] = mapped_column(DateTime)    # naive UTC
    description: Mapped[str] = mapped_column(Text, default="")
    rrule: Mapped[str] = mapped_column(Text, default="")  # iCal RRULE string when recurring
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    home: Mapped["Home"] = relationship("Home")
    resource: Mapped["HomeResource"] = relationship("HomeResource", back_populates="reservations")
    user: Mapped["User"] = relationship("User")
