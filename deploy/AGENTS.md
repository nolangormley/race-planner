# deploy

The server side of the CI deploy to yasmodeus.
The GitHub workflow pipes a tar of the repo over SSH, and this folder controls what the server does with it.

- `bootstrap.sh`: run once as root on a new server, before `install.sh`. It creates the `raceplanner` user, the Postgres database, the Python 3.12 venv, and installs the env file and the optional DuckDB seed.
- `install.sh`: run as root. It installs the files below and writes `/opt/race-planner/.ssh/authorized_keys`.
- `receive.sh`: installed as `/usr/local/bin/race-planner-receive`, the forced command for the deploy key. It syncs the code into `/opt/race-planner/app`, installs the requirements, restarts the service, and checks that `/login` answers.
- `race-planner.service`: the systemd unit. It runs the Alembic migrations, then uvicorn on `127.0.0.1:8000`. nginx proxies the public site to it.
- `sudoers`: lets `raceplanner` restart its own service and nothing else.
- `ci_deploy_key.pub`: the public deploy key. The private half is the `DEPLOY_SSH_KEY` repo secret.

The deploy does not touch runtime state: `/opt/race-planner/.env` holds the secrets, and `/opt/race-planner/app/data/` holds the DuckDB warehouse.
A change here has no effect until `install.sh` runs again on the server.
