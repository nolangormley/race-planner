"""The coaching note on the dashboard: one short LLM call grounded in the
athlete's current training status."""
import logging

from src.api.services.llm import (
    call_llm, get_llm_provider, LLMConfigError,
    TRAINING_BENCHMARKS, NO_PSEUDOSCIENCE_CLAUSE, NEVER_CONTRADICT_STANDING_CLAUSE,
)
from src.api.services.metrics import get_performance_standing

logger = logging.getLogger("race_planner.coach")

def get_ai_insight(stats, context="status", workout=None):
    try:
        history_str = "\n".join([f"  - {h['date']}: Form (TSB): {h['form']}, Load (ATL): {h['fatigue']}" for h in stats.get('history', [])])

        standings = get_performance_standing(stats)

        if context == "status":
            prompt = (
                f"Explain the user's current training status based on these metrics and standings:\n"
                f"- Fitness (CTL): {stats.get('fitness_ctl')} (Standing: {standings['ctl_standing']})\n"
                f"- Fatigue (ATL): {stats.get('fatigue_atl')}\n"
                f"- Form (TSB): {stats.get('form_tsb')} (Standing: {standings['tsb_standing']})\n"
                f"- Estimated VO2 Max: {stats.get('latest_vo2_max', 'N/A')} (Standing: {standings['vo2_standing']})\n"
                f"- Acute-to-Chronic Workload Ratio (ACWR): {stats.get('acwr')}\n"
                f"- 7-Day Efficiency Factor: {stats.get('efficiency_factor_7d')}\n"
                f"- 7-Day Aerobic Decoupling: {stats.get('aerobic_decoupling_7d')}%\n\n"
                f"Give a HIGH-LEVEL EXECUTIVE SUMMARY of their readiness. "
                f"Focus on what action they need to take today. "
                f"Mention their ACWR (sweet spot 0.8-1.3). No fluff, no raw token dumps. Keep it professional and scannable."
            )
        elif context == "workout":
            prompt = (
                f"The athlete currently has a Form/TSB of {stats.get('form_tsb')} (Negative TSB means fatigued/in heavy training, positive means rested/tapering).\n"
                f"Their 7-day Aerobic Decoupling is {stats.get('aerobic_decoupling_7d')}% (Under 5% indicates good base aerobic fitness).\n"
                f"Their Estimated VO2 Max is: {stats.get('latest_vo2_max', 'N/A')}\n\n"
                f"We are recommending this workout: {workout.get('name')} ({workout.get('category')}).\n"
                f"Description: {workout.get('description')}\n\n"
                f"Briefly explain in 2-3 sentences why this specific workout is appropriate for their current training state, acting as their professional endurance coach. Talk directly to them."
            )

        weight_str = f"weighs {stats.get('weight_lbs')}lbs" if stats.get('weight_lbs') else "weighs 220lbs"
        sex_str = stats.get('sex', 'male')
        if sex_str == 'M': sex_str = 'male'
        elif sex_str == 'F': sex_str = 'female'

        user_race_type = stats.get('race_type')
        user_race_date = stats.get('race_date')
        user_goal_time = stats.get('goal_time')

        race_info = ""
        if user_race_type and user_race_date:
            race_info = f"training for a {user_race_type} on {user_race_date}"
            if user_goal_time:
                race_info += f" with a goal time of {user_goal_time}"
            race_info += "."

        system_prompt = (
            f"You are an expert running coach and one of your athletes is {race_info} The athlete is {sex_str}, and {weight_str}. "
            f"{TRAINING_BENCHMARKS}"
            f"Provide actionable advice as concise bullet points. {NEVER_CONTRADICT_STANDING_CLAUSE}"
            f"CRITICAL CONSTRAINTS: "
            f"1) Recommending a MAXIMUM of ONE training action for the current day. "
            f"2) {NO_PSEUDOSCIENCE_CLAUSE}"
            f"3) Output plain text only. Do NOT use markdown or HTML tags. Use only simple hyphens (-) for bullet points. "
            f"4) KEEP IT EXTREMELY BRIEF. Provide exactly 1 short introductory sentence, followed by 2-3 short bullet points."
        )

        # Small model for groq here (fast, short answer) — get_ai_training_plan
        # and training_plan_chat deliberately use a bigger groq model since
        # they generate structured JSON plans, not a one-paragraph insight.
        model = "llama-3.1-8b-instant" if get_llm_provider() == "groq" else None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        try:
            # 30s timeout — previously this call had none at all (could hang
            # a request thread indefinitely); see
            # agent_prompting/improvement_plan.md Section 3.
            return call_llm(messages, model=model, timeout=30)
        except LLMConfigError as e:
            # Covers both "API key not set" and "unexpected response shape"
            # — the latter used to `return str(data)` here, i.e. a raw
            # provider response dict rendered as if it were coaching text.
            return str(e)

    except Exception as e:
        print(f"Error getting AI insight: {e}")
        return None
