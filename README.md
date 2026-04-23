# Race Planner & Activity Warehouse

A professional-grade training analytics platform and AI coaching engine built on your Strava data. This tool transforms raw activity streams into actionable physiological insights and customized training programs.

## 🚀 Key Features

*   **Training Load Analytics**: Visualize your Fitness (CTL), Fatigue (ATL), and Form (TSB) using the Banister impulse-response model.
*   **AI-Powered Race Coaching**: Select a target race from your calendar, and our AI (via Groq/Llama 3) builds a periodized training block tailored to your current fitness levels.
*   **Deep Activity Analysis**:
    *   **Efficiency Factor (EF)**: Tracking aerobic efficiency over time.
    *   **Aerobic Decoupling (Pa:Hr)**: Measure how well your heart rate holds steady during endurance runs.
    *   **Pace Zone Mapping**: Automatic calculation of physiological zones (Recovery to Anaerobic) based on your recent 6-week performance.
*   **Performance Metrics Warehouse**: All data is stored locally in a high-performance **DuckDB** warehouse for lightning-fast queries and long-term history tracking.
*   **Modern Web UI**: A glass-morphic, responsive dashboard with interactive charts (Chart.js) and real-time AI executive summaries.

## 🛠 Setup

1.  **Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configuration**:
    Create a `.env` file in the root directory:
    ```env
    STRAVA_CLIENT_ID=your_id
    STRAVA_CLIENT_SECRET=your_secret
    GROQ_API_KEY=your_key
    ```

3.  **Authentication**:
    Run the ingestion pipeline to authenticate with Strava and populate your warehouse:
    ```bash
    python src/pipeline.py
    ```

## 📈 Usage

### Running the Web Dashboard
The primary way to interact with the platform is via the web application.

**Option A: Local Uvicorn** (Recommended for development)
```bash
python -m uvicorn src.api.main:app --reload
```

**Option B: Docker Compose**
```bash
docker-compose up --build
```
Access the dashboard at `http://localhost:8000`.

### Data Ingestion
To sync your latest Strava activities and recalculate effectiveness metrics:
```bash
python src/pipeline.py
```

## 🏗 Project Structure

*   **/src/api**: FastAPI backend and Jinja2 templates (Dashboard, Warehouse, Activity Analysis).
*   **/src/pipeline.py**: The ingestion and analysis engine that calculates TRIMP, EF, and TSB.
*   **/data**: Contains the `strava_warehouse.duckdb` file.
*   **analyze_effectiveness.py**: Core logic for calculating physiological metrics from activity streams.

## 📊 Methodology
*   **Training Load**: Uses the Banister TRIMP model where `Fitness (CTL)` is a 42-day exponentially weighted moving average (EWMA) and `Fatigue (ATL)` is a 7-day EWMA.
*   **Pace Zones**: Calculated using a proprietary algorithm that analyzes the 95th percentile of your speed over the last 6 weeks to estimate Threshold Pace.
