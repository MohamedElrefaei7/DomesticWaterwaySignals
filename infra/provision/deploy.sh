#!/usr/bin/env bash
#
# Deploy. Pull, build, bring the stack up. Run by a human on the instance; no agent connects here.
#
# ===============================================================================================
# THE DEPLOY DIRECTORY IS A FIXED ABSOLUTE CONSTANT AND IS NEVER DERIVED FROM THIS SCRIPT
# ===============================================================================================
#
# CLAUDE.md § 5: the canonical value is /opt/inland-waterway-signals and every provisioning and
# deploy script references this constant; none derives it from $0, $PWD, `dirname`, `realpath`, or
# any other contextual source. A path derived from the script's own location is a path that means
# something different when the script is run from a copy — which is exactly what happens when
# somebody scp's a directory to try something out.
#
# ===============================================================================================
# A NOTE ON CLAUDE.md § 5's "REFUSE TO RUN IF THE TARGET CONTAINS A .git DIRECTORY"
# ===============================================================================================
#
# THIS SCRIPT DOES THE OPPOSITE: it refuses to run when the target does NOT contain one, and that
# is deliberate. The two halves of that § 5 bullet describe two different scripts. The refusal
# clause guards a staging target that something `rsync --delete`s into, where a git checkout
# underneath would be silently destroyed. The deploy path in the same section is "`git pull` on
# the server, then a provisioning script, then `docker compose up -d`" — and a `git pull` needs a
# checkout, so requiring `.git` here is that path's own precondition, not a relaxation of it.
#
# What survives from the refusal clause is the property it was protecting, and this script holds
# it directly: THERE IS NO `rsync`, NO `--delete`, AND NO RECURSIVE REMOVAL ANYWHERE IN THIS FILE.
# Nothing here can destroy the checkout it operates on. The tension is recorded in the Phase 10
# commit report and in CLAUDE.md § 22 rather than resolved silently.
#
# ===============================================================================================
# WHAT THIS SCRIPT DOES NOT DO
# ===============================================================================================
#
#   - It does not apply schema changes. Those are a CLI a human invokes, never a deploy step and
#     never a container start step (CLAUDE.md § 3). A deploy that changes the schema every time it
#     runs is a rollback that cannot roll back.
#   - It does not touch .env. The secret is the human's and this script never reads or echoes it;
#     it only checks that the file exists.
#   - It does not run `terraform` anything, and it does not touch the firewall.
#
# Usage:
#   sudo -u <deploy user> infra/provision/deploy.sh
#   infra/provision/deploy.sh --dry-run                 # print the commands, run none of them
#   infra/provision/deploy.sh --deploy-dir /tmp/x       # test-only; the default is the constant

set -euo pipefail

# ---------------------------------------------------------------------------------------------
# The constant. CLAUDE.md § 5.
# ---------------------------------------------------------------------------------------------
DEPLOY_DIR_DEFAULT="/opt/inland-waterway-signals"

DEPLOY_DIR="$DEPLOY_DIR_DEFAULT"
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --deploy-dir)
            # Exists so tests/deploy/test_deploy_script.py can exercise the guards against a
            # tmp_path without an instance (CLAUDE.md § 9). The DEFAULT is the constant above and
            # is not computed from anything.
            DEPLOY_DIR="${2:?--deploy-dir requires a path}"
            shift 2
            ;;
        -h|--help)
            # Spelled out rather than read back out of this file. Printing the header would mean
            # reading a path derived from the script's own location, which is the one thing
            # CLAUDE.md § 5 says a deploy script never does - even for something as harmless as
            # help text, because the next person copies the idiom.
            cat <<'USAGE'
deploy.sh - pull, build, and bring up the Inland Waterway Signals stack.

  deploy.sh                      deploy into /opt/inland-waterway-signals
  deploy.sh --dry-run            print every command, run none of them
  deploy.sh --deploy-dir PATH    test-only override; the default is the constant above

Applies no schema changes and touches no secret. See the header of this file.
USAGE
            exit 0
            ;;
        *)
            echo "deploy: unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf 'DRY-RUN  %s\n' "$*"
    else
        printf '+ %s\n' "$*"
        "$@"
    fi
}

fail() {
    echo "deploy: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------------------------
# Guards. Every one of these reports the observed value, never a bare refusal (CLAUDE.md § 13).
# ---------------------------------------------------------------------------------------------

[ -d "$DEPLOY_DIR" ] || fail "deploy directory does not exist: $DEPLOY_DIR"

# The checkout guard. See the header: this script operates ON a checkout, so its absence means
# this is not the deploy directory — most likely an unpacked copy or an empty path someone created
# by hand — and pulling into it would either fail confusingly or succeed against the wrong tree.
[ -d "$DEPLOY_DIR/.git" ] || fail \
    "no .git directory under $DEPLOY_DIR - this is not the deploy checkout. The deploy path is a
     git checkout at $DEPLOY_DIR_DEFAULT that this script pulls into (CLAUDE.md § 5). Copying a
     directory here by hand is not a deployment mechanism; it caused four stale-file incidents in
     one session on the prior project."

# .env is where every secret in this stack comes from, and docker compose reads it from the
# working directory. Its absence is a stack that comes up with an unset POSTGRES_PASSWORD and an
# unset API_DATABASE_URL — both of which are `:?` in docker-compose.yml, so the failure is loud,
# but it is loud several minutes later and after a build.
#
# THE FILE IS CHECKED FOR EXISTENCE AND NEVER READ. Nothing in this script echoes a secret.
[ -f "$DEPLOY_DIR/.env" ] || fail \
    "no .env under $DEPLOY_DIR - copy .env.example and fill it in on this machine (CLAUDE.md § 1:
     the values are generated here and never pasted into a chat, a ticket, or an agent session)."

[ -f "$DEPLOY_DIR/docker-compose.yml" ] || fail "no docker-compose.yml under $DEPLOY_DIR"
[ -f "$DEPLOY_DIR/Caddyfile" ] || fail "no Caddyfile under $DEPLOY_DIR"

cd "$DEPLOY_DIR"

echo "deploy: target $DEPLOY_DIR"

# ---------------------------------------------------------------------------------------------
# 1. Pull. --ff-only so a diverged local tree stops the deploy instead of producing a merge commit
#    nobody asked for on a server nobody develops on.
# ---------------------------------------------------------------------------------------------
run git pull --ff-only

# ---------------------------------------------------------------------------------------------
# 2. BUILD THE FRONTEND FIRST, AND THE ORDER IS THE POINT.
#
#    The bundle is produced inside the pinned Node image (Dockerfile.frontend) at IMAGE BUILD
#    time, so this line is the frontend build. Doing it before `up -d` means a build failure stops
#    the deploy with the previous bundle still being served, rather than after the stack has been
#    recreated around a broken one. Phase 9 found the instance's own Node too old to run this
#    build at all; that host dependency is now gone, because the host's Node is never invoked.
#
#    tests/deploy/test_deploy_script.py asserts this line comes before the `up -d` line.
# ---------------------------------------------------------------------------------------------
run docker compose build frontend-build api

# ---------------------------------------------------------------------------------------------
# 3. Up. Compose starts timescaledb, waits for it to be healthy, starts api, runs frontend-build
#    to completion, and only then starts caddy.
#
#    NO SCHEMA STEP HERE AND NONE IN ANY CONTAINER'S START PATH (CLAUDE.md § 3).
# ---------------------------------------------------------------------------------------------
run docker compose up -d

# ---------------------------------------------------------------------------------------------
# 4. Report what actually came up. PORTS should be populated on caddy and EMPTY on everything
#    else; that is the published-port set being {80, 443} observed rather than asserted.
# ---------------------------------------------------------------------------------------------
run docker compose ps

cat <<'EOF'

deploy: done. Two things to look at before calling it good:

  1. `docker compose ps` above - PORTS must be populated ONLY on caddy. Anything on api or
     timescaledb is a published port that bypasses the edge entirely.
  2. `docker compose logs -f caddy` - watch the ACME exchange. If issuance fails, READ THE ERROR
     AND WAIT. Let's Encrypt rate-limits failed issuance per domain per week; restarting the
     stack in a loop turns a wait of minutes into a lockout of days.
EOF
