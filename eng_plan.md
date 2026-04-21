# Engineering Execution Plan: Fixing VP Review #3

This document outlines the exact technical implementation plan to address the latest feedback.

### 1. Heart Rate Zones & Designed Empty States
**Issue:** The VP assumed heart rate data ranging into the 120,000s were "massive dummy values." In reality, the database queries were effectively summing absolute `time_offset` seconds across all historical workouts, leading to extremely large, unusable raw second totals that do not map to user intuition. 
**Implementation Plan:**
- **Backend (`main.py`):** Keep the `/analytics/{user_id}` route accurately returning total seconds.
- **Frontend (`dashboard.html`):** Intercept the `time_seconds` and convert them into readable formats like Hours/Minutes, or better yet, convert the raw data into Percentage-based distribution for the Chart.js visualization. 
- **Empty States Check:** Ensure that if `data.hr_zones` equals `0` or is actually empty, the chart correctly falls back to the SVG Empty State.

### 2. Customization State Management (Fixing the Theater)
**Issue:** Toggling Theme, Layout, and Units fired rudimentary JavaScript but failed to persist user state and lacked robust dom hierarchy updating.
**Implementation Plan:**
- **State Persistence:** Utilize `localStorage` to save user preferences (`theme`, `units`, `layout`) so the changes dynamically load and persist through page reloads.
- **Theme Engine Fix:** The CSS pseudo-selectors for `[data-theme="light"]` will be explicitly mapped to override `body` styles actively.
- **Layout & Units Verification:** Hook up metric conversion not just to the schedule fetch but locally rendering the distance strings.

### 3. Restricting AI Hallucinations (Prompt Engineering)
**Issue:** The LLM is misinterpreting statistical ranges (calling a 14.8 CTL "Optimized") and blindly telling users to do 3 hardcore workouts in a single day. 
**Implementation Plan:**
- **Backend Prompt Refactor (`main.py`):** 
    - Inject strict reference ranges directly into the system prompt: *(CTL < 30 = Beginner, 50-80 = Intermediate, >100 = Advanced)*.
    - Add an explicit negative constraint: *DO NOT hallucinate pseudo-science. Running depletes energy. Rest restores it.*
    - Enforce a strict daily limit: *You are permitted to recommend a MAXIMUM of ONE training action for the current day.*
    - Constrain interpretation of metrics rigidly.
