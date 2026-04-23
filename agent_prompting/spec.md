# Race Planner

Race Planner is a web application that will help athletes plan their training for a specific race or goal. It will use the Strava API to get the user's data that it will provide insights off of. It will use the data to build a beautiful corpus of statistics and insights that will fill a dashboard to show the athlete. This will be the main dashboard that the user will look at when they first log in after they finish the initial account setup. Initially, this will only be used for running races, but in the future it will be expanded to include cycling and triathlon races.

## Persona
You are a senior software engineer with experience in building web applications and APIs. You are also a sports analyst and understand the basics of training and racing. Alongside this, you have a strong understanding of how to use statistics to analyse sports data and provide insights to athletes.

## Tech Stack

- Python
- FastAPI
- PostgreSQL
- Docker
- Strava API

## Strava Connection

This app will connect to Strava using the Strava API. It will use the Strava API to get the user's activities and data streams associated with them.

clone this https://github.com/nolangormley/strava-recommended-workout and use it as a starting point for the app.

## Workflow of the website

- login or create account
    - if the user needs to create an account the following workflow happens
        - the user is asked to provide the following information
            - email
            - password
            - name
            - date of birth
            - gender
            - height
            - weight
        - the user is then asked what kind of race they are training for (length of race, date of race, and goal time)
        - user is redirected to the Strava API to authorize the app
        - user is redirected back to the app with a code
        - app exchanges the code for an access token
        - app gets the user's data from the Strava API
        - app creates an account for the user
        - user is redirected to the dashboard (the strava ingestion will likely take 15-20 minutes, so give them a message that the data is being ingested and they will be notified when it is ready)
- dashboard
    - the dashboard is the main page of the app and will be displayed to the user when they log in
    - it will show the user's training data and insights
    - it will also show the user's race plan
    - the dashboard will be updated in real-time as the user logs more activities
    - The strava-recommended-workout repo mentioned above calculates a lot of metrics that you will likely use. Use them as a starting place, but don't feel like you need to stick with only those metrics.
    - Most of all, the dashboard should be beautiful and easy to understand. It should be a place that the user will want to look at every day.
    - Use graphs and charts to display the data in a way that is easy to understand.
