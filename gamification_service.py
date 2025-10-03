from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from model import User, Habit, HabitCheckIn, UserBadge, BadgeType
from datetime import datetime, timedelta

class GamificationService:
    
    @staticmethod
    async def award_points(user_id: int, points: int, db: AsyncSession):
        """Award points to user and update level"""
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if user:
            user.total_points += points
            # Level up every 1000 points
            user.level = (user.total_points // 1000) + 1
            await db.commit()
    
    @staticmethod
    async def check_and_award_badges(user_id: int, db: AsyncSession) -> List[UserBadge]:
        """Check for new badges and award them"""
        newly_awarded = []
        
        # Get user data
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return newly_awarded
        
        # Get existing badges
        existing_badges_result = await db.execute(
            select(UserBadge).where(UserBadge.user_id == user_id)
        )
        existing_badges = {badge.badge_type for badge in existing_badges_result.scalars().all()}
        
        # Check for streak badges
        streak_badges = await GamificationService._check_streak_badges(user_id, db, existing_badges)
        newly_awarded.extend(streak_badges)
        
        # Check for habit creation badges
        creation_badges = await GamificationService._check_creation_badges(user_id, db, existing_badges)
        newly_awarded.extend(creation_badges)
        
        # Check for consistency badges
        consistency_badges = await GamificationService._check_consistency_badges(user_id, db, existing_badges)
        newly_awarded.extend(consistency_badges)
        
        # Save new badges
        for badge in newly_awarded:
            db.add(badge)
        
        if newly_awarded:
            await db.commit()
        
        return newly_awarded
    
    @staticmethod
    async def _check_streak_badges(user_id: int, db: AsyncSession, existing_badges: set) -> List[UserBadge]:
        """Check for streak-based badges"""
        badges = []
        
        # Get user's best current streak
        result = await db.execute(
            select(Habit).where(and_(Habit.user_id == user_id, Habit.is_active == True))
        )
        habits = result.scalars().all()
        
        max_streak = 0
        for habit in habits:
            # Calculate current streak for this habit
            checkins_result = await db.execute(
                select(HabitCheckIn)
                .where(HabitCheckIn.habit_id == habit.id)
                .order_by(HabitCheckIn.check_in_date.desc())
            )
            checkins = checkins_result.scalars().all()
            
            streak = GamificationService._calculate_streak(checkins)
            max_streak = max(max_streak, streak)
        
        # Award streak badges
        if max_streak >= 30 and BadgeType.MONTH_MASTER not in existing_badges:
            badges.append(UserBadge(
                user_id=user_id,
                badge_type=BadgeType.MONTH_MASTER,
                badge_name="Month Master",
                badge_description="Maintained a habit for 30 consecutive days!"
            ))
        elif max_streak >= 7 and BadgeType.WEEK_WARRIOR not in existing_badges:
            badges.append(UserBadge(
                user_id=user_id,
                badge_type=BadgeType.WEEK_WARRIOR,
                badge_name="Week Warrior",
                badge_description="Maintained a habit for 7 consecutive days!"
            ))
        elif max_streak >= 3 and BadgeType.STREAK_STARTER not in existing_badges:
            badges.append(UserBadge(
                user_id=user_id,
                badge_type=BadgeType.STREAK_STARTER,
                badge_name="Streak Starter",
                badge_description="Started your first 3-day streak!"
            ))
        
        return badges
    
    @staticmethod
    async def _check_creation_badges(user_id: int, db: AsyncSession, existing_badges: set) -> List[UserBadge]:
        """Check for habit creation badges"""
        badges = []
        
        # Count total habits created
        result = await db.execute(
            select(func.count(Habit.id)).where(Habit.user_id == user_id)
        )
        total_habits = result.scalar()
        
        if total_habits >= 5 and BadgeType.HABIT_CREATOR not in existing_badges:
            badges.append(UserBadge(
                user_id=user_id,
                badge_type=BadgeType.HABIT_CREATOR,
                badge_name="Habit Creator",
                badge_description="Created 5 different habits!"
            ))
        
        return badges
    
    @staticmethod
    async def _check_consistency_badges(user_id: int, db: AsyncSession, existing_badges: set) -> List[UserBadge]:
        """Check for consistency badges"""
        badges = []
        
        # Calculate overall completion rate for last 30 days
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        
        # This is a simplified calculation - you might want to make it more sophisticated
        habits_result = await db.execute(
            select(Habit).where(and_(Habit.user_id == user_id, Habit.is_active == True))
        )
        habits = habits_result.scalars().all()
        
        if habits:
            total_expected = len(habits) * 30  # Assuming daily habits
            
            checkins_result = await db.execute(
                select(func.count(HabitCheckIn.id))
                .join(Habit)
                .where(and_(
                    Habit.user_id == user_id,
                    HabitCheckIn.check_in_date >= thirty_days_ago
                ))
            )
            total_checkins = checkins_result.scalar()
            
            completion_rate = (total_checkins / total_expected) * 100 if total_expected > 0 else 0
            
            if completion_rate >= 90 and BadgeType.CONSISTENCY_KING not in existing_badges:
                badges.append(UserBadge(
                    user_id=user_id,
                    badge_type=BadgeType.CONSISTENCY_KING,
                    badge_name="Consistency King",
                    badge_description="Achieved 90% completion rate for 30 days!"
                ))
        
        return badges
    
    @staticmethod
    def _calculate_streak(checkins: List[HabitCheckIn]) -> int:
        """Calculate current streak from checkins"""
        if not checkins:
            return 0
        
        # Sort by date (most recent first)
        sorted_checkins = sorted(checkins, key=lambda x: x.check_in_date, reverse=True)
        
        streak = 0
        current_date = datetime.utcnow().date()
        
        for checkin in sorted_checkins:
            checkin_date = checkin.check_in_date.date()
            expected_date = current_date - timedelta(days=streak)
            
            if checkin_date == expected_date:
                streak += 1
            else:
                break
        
        return streak