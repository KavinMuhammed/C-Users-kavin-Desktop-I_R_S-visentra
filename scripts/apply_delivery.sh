#!/bin/bash
# apply_delivery.sh NAME  (run from the project root; files in ../backups/NAME.*)
# add = new file; replace_unmodified = laptop copy equals the version the update was based on; CONFLICT = laptop copy
# differs from both -> kept, never overwritten. Every replaced file is backed up first.
set -u; N=$1; B=../backups/$N; T=$(date -u +%Y%m%d-%H%M%S); BK=../backups/before-$N-$T; mkdir -p "$BK"
paste -d'\t' $B.sha256 $B.base.sha256 $B.files.txt > /tmp/$N.tsv; : > /tmp/$N.plan
while IFS=$'\t' read -r new old f; do
  if [ ! -e "$f" ]; then st=add; else cur=$(sha256sum "$f" | cut -c1-64)
    if [ "$cur" = "$new" ]; then st=same; elif [ "$cur" = "$old" ]; then st=replace_unmodified; else st=CONFLICT; fi; fi
  printf '%s\t%s\n' "$st" "$f" >> /tmp/$N.plan; done < /tmp/$N.tsv
awk -F'\t' '$1=="replace_unmodified"||$1=="CONFLICT"{print $2}' /tmp/$N.plan | while IFS= read -r f; do mkdir -p "$BK/$(dirname "$f")"; cp -p "$f" "$BK/$f"; done
awk -F'\t' '$1=="add"||$1=="replace_unmodified"{print $2}' /tmp/$N.plan > /tmp/$N.apply
[ -s /tmp/$N.apply ] && tar --overwrite -xzf $B.tar.gz -T /tmp/$N.apply
cp /tmp/$N.plan "$BK/PLAN.tsv"; bad=0
while IFS=$'\t' read -r new old f; do st=$(awk -F'\t' -v f="$f" '$2==f{print $1}' /tmp/$N.plan); [ "$st" = CONFLICT ] && continue
  [ "$(sha256sum "$f" | cut -c1-64)" = "$new" ] || { echo "MISMATCH $f"; bad=$((bad+1)); }; done < /tmp/$N.tsv
cut -f1 /tmp/$N.plan | sort | uniq -c; grep CONFLICT /tmp/$N.plan; echo "mismatches=$bad backup=$BK"
