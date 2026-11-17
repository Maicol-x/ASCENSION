#!/usr/bin/env python3
"""Combine coverage + health sample into one human report."""
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PUB = os.path.join(ROOT, "public")
DATA = os.path.join(HERE, "data")

cov = json.load(open(os.path.join(DATA, "coverage.json"), encoding="utf-8"))
hs = json.load(open(os.path.join(DATA, "health_summary.json"), encoding="utf-8"))
core = json.load(open(os.path.join(PUB, "cameras.core.json"), encoding="utf-8"))

n = core["count"]
src_dict = core["srcDict"]
counts = Counter()
displayable = 0
for i in range(n):
    if core["de"][i] == 1 or core["live"][i] == 1:
        displayable += 1
        counts[src_dict[core["src"][i]]] += 1

ov = hs["overall_uniform"]
rate = ov["rate_pct"] / 100
est = round(displayable * rate)
lo = round(displayable * ov["ci95"][0] / 100)
hi = round(displayable * ov["ci95"][1] / 100)

lines = []
lines.append("NEXUS — COBERTURA Y SALUD REAL DE LA RED")
lines.append("=" * 60)
lines.append(f"Dataset total              : {n:,}")
lines.append(f"Cámaras mostrables         : {displayable:,}")
lines.append("")
lines.append("COBERTURA GEOGRÁFICA")
lines.append(f"  Países con ≥1 cámara     : {cov['countries_with_cameras']} / 195 (ONU)")
lines.append(f"  Celdas 0.1° ocupadas     : {cov['occupied_cells_0.1deg']:,}")
for r in cov["radii"]:
    lines.append(
        f"  A ≤{r['radius_km']:>2} km : {r['land_pct']:>5.2f}% de la tierra"
        f"  |  {r['surface_pct']:>5.2f}% del planeta"
    )
lines.append("")
lines.append("SALUD REAL DE LOS FEEDS (muestra)")
lines.append(f"  Muestra uniforme         : {ov['n']:,} cámaras")
lines.append(f"  Responden                : {ov['ok']:,}  ({ov['rate_pct']:.1f}%)")
lines.append(f"  IC 95%                   : {ov['ci95'][0]:.1f}% – {ov['ci95'][1]:.1f}%")
lines.append(f"  Estimación en toda la red: {est:,} cámaras funcionando ({lo:,}–{hi:,})")
lines.append(f"  Puntos que nunca abren   : {displayable - est:,}")
lines.append("")
lines.append(f"  Vía directa              : {hs['success_via'].get('direct', 0):,} de la muestra")
lines.append(f"  Vía proxy local          : {hs['success_via'].get('proxy', 0):,} de la muestra")
lines.append("")
lines.append("POR TIPO DE FEED")
for ft, r in sorted(hs["by_feed_type"].items(), key=lambda kv: -kv[1]["n"]):
    lines.append(f"  {ft:<14} n={r['n']:<5} {r['rate_pct']:>5.1f}%  IC {r['ci95'][0]:.0f}–{r['ci95'][1]:.0f}%")
lines.append("")
lines.append("POR FUENTE (las 20 mayores)")
lines.append(f"  {'fuente':<28}{'cámaras':>10}{'muestra':>9}{'éxito':>8}{'estimadas OK':>14}")
for src, total in counts.most_common(20):
    r = hs["by_source"].get(src)
    if r:
        est_s = round(total * r["rate_pct"] / 100)
        lines.append(f"  {src[:27]:<28}{total:>10,}{r['n']:>9}{r['rate_pct']:>7.1f}%{est_s:>14,}")
    else:
        est_s = round(total * rate)
        lines.append(f"  {src[:27]:<28}{total:>10,}{'—':>9}{'—':>7}{est_s:>14,}*")
lines.append("")
lines.append("  * sin muestra propia: se aplica la tasa global")
lines.append("")
lines.append("MOTIVOS DE FALLO (muestra)")
for reason, c in list(hs["failure_reasons"].items())[:10]:
    lines.append(f"  {reason[:44]:<46}{c:>6}")
lines.append("")
lines.append(f"Calculado: {hs['probed_at']}")

report = "\n".join(lines)
out = os.path.join(DATA, "health_report.txt")
open(out, "w", encoding="utf-8").write(report)
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
print(report)
print("\nGuardado en", out)
