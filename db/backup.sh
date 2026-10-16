#!/bin/sh
# The backup service (spec: «ежедневный pg_dump в отдельный том»). Runs in the postgres image: pg_dump of the same major.
#
#   backup.sh loop    # every day at BACKUP_AT (TZ): a dump into /backups, the newest BACKUP_KEEP kept
#   backup.sh now     # one dump right away
#   backup.sh check   # restore the newest dump into a scratch database, compare row counts with the live one, drop it
#
# Every dump writes a row to runs (job 'backup'), a failed one with the error.
set -u
DIR=/backups
KEEP=${BACKUP_KEEP:-7}
TABLES="sources items stories digests digest_entries feedback taste_profiles drafts runs"

record() {  # $1 stats json, $2 error or empty
  # variables are interpolated in stdin, not in -c
  echo "insert into runs (job, finished_at, stats, error) values ('backup', now(), :'stats'::jsonb, nullif(:'err', ''))" |
    psql -qAt -v ON_ERROR_STOP=1 -v stats="$1" -v err="$2" || echo "backup: could not write to runs"
}

dump() {
  f="$DIR/radar-$(date +%Y%m%d-%H%M).dump"
  start=$(date +%s)
  if err=$(pg_dump -Fc -f "$f.tmp" 2>&1) && mv "$f.tmp" "$f"; then
    ls -1t "$DIR"/radar-*.dump | tail -n +$((KEEP + 1)) | xargs -r rm -f
    stats=$(printf '{"file": "%s", "bytes": %s, "seconds": %s, "kept": %s}' \
      "$(basename "$f")" "$(stat -c %s "$f")" $(($(date +%s) - start)) "$(ls -1 "$DIR"/radar-*.dump | wc -l)")
    echo "backup: $stats"
    record "$stats" ""
  else
    rm -f "$f.tmp"
    echo "backup failed: $err"
    record '{}' "$(printf '%s' "$err" | head -c 500)"
    return 1
  fi
}

check() {
  f=$(ls -1t "$DIR"/radar-*.dump 2>/dev/null | head -1)
  [ -n "$f" ] || { echo "check: no dumps in $DIR"; return 1; }
  db=radar_restore_check
  psql -q -c "drop database if exists $db" -c "create database $db" || return 1
  pg_restore --no-owner -d "$db" "$f" || { psql -q -c "drop database $db"; return 1; }
  echo "check: $(basename "$f") restored; rows live / restored:"
  rc=0
  for t in $TABLES; do
    live=$(psql -qAt -c "select count(*) from $t")
    got=$(psql -qAt -d "$db" -c "select count(*) from $t")
    echo "  $t: $live / $got"
    # the live tables only grow after the dump (runs, items) — restored must not be empty where live is not
    [ "$got" -gt 0 ] || [ "$live" -eq 0 ] || rc=1
  done
  psql -q -c "drop database $db"
  [ $rc -eq 0 ] && echo "check: ok" || echo "check: a table came back empty"
  return $rc
}

case "${1:-loop}" in
  now) dump ;;
  check) check ;;
  loop)
    mkdir -p "$DIR"
    echo "backup: daily at $BACKUP_AT $TZ, keep $KEEP"
    while true; do
      now=$(date +%s)
      next=$(date -d "today $BACKUP_AT" +%s)
      [ "$next" -gt "$now" ] || next=$(date -d "tomorrow $BACKUP_AT" +%s)
      sleep $((next - now))
      dump
    done
    ;;
  *) echo "usage: backup.sh loop|now|check"; exit 2 ;;
esac
