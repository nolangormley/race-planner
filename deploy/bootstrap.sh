#!/bin/bash
# Prepares a new server for race-planner. Run once as root, before install.sh.
# Usage: bootstrap.sh ENV_FILE [WAREHOUSE]
# ENV_FILE becomes /opt/race-planner/.env. WAREHOUSE, if given, seeds the DuckDB warehouse.
set -euo pipefail

env_file=$1
warehouse=${2:-}
home=/opt/race-planner

apt-get install -y postgresql
# Socket only, so Postgres does not take 127.0.0.1:5432 from the Airflow container.
sudo -u postgres psql -qc "ALTER SYSTEM SET listen_addresses = ''"
systemctl restart postgresql

useradd --system --home-dir "$home" --create-home --shell /bin/sh raceplanner
sudo -u postgres createuser raceplanner
sudo -u postgres createdb --owner raceplanner raceplanner

install -o raceplanner -g raceplanner -m 750 -d "$home"/app "$home"/app/data
install -o raceplanner -g raceplanner -m 600 "$env_file" "$home"/.env
if [[ -n $warehouse ]]; then
  install -o raceplanner -g raceplanner -m 640 "$warehouse" "$home"/app/data/strava_warehouse.duckdb
fi

curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
# uv reads config from the working directory, so it must be one that raceplanner can read.
cd "$home"
sudo -u raceplanner -H /usr/local/bin/uv venv --python 3.12 "$home"/.venv

echo "bootstrap done, now run install.sh"
