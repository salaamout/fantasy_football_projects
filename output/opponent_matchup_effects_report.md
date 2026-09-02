# Opponent Matchup Effects — Projections vs. Actual Results

Generated: September 1, 2026
Script: `analysis/opponent_matchup_analysis.py`

## Overview

Two runs of the same player-centered-residual + one-way-ANOVA analysis were
performed to answer: *does a player's fantasy output vary systematically
depending on which team they're facing that week, beyond what's explained by
their own overall talent/role?*

1. **Projections (forward-looking, 2026 season, weeks 1–18)** — Sleeper's
   own preseason weekly projections for the upcoming 2026 season.
   Command: `python -m analysis.opponent_matchup_analysis --source projections --season 2026 --weeks 1-18 --positions QB,RB,WR,TE`
   Output data: `output/opponent_matchup_effects.csv`

2. **Actual results (historical, 2025 season, weeks 1–18)** — Sleeper's
   actual final stats for every game played in the 2025 season.
   Command: `python -m analysis.opponent_matchup_analysis --source stats --season 2025 --weeks 1-18`
   Output data: `output/opponent_matchup_effects_actual_2025.csv`

For each position, players are "centered" by subtracting their own average
points across the season, then residuals are grouped by opponent. A large
negative `avg_residual` means that position historically/projected
underperforms its own average against that opponent (a tough matchup); a
large positive value means it overperforms (an easy/favorable matchup).
`residual_z` is the average residual divided by its standard error — a rough
significance indicator for that specific opponent row (not the same as the
overall ANOVA p-value for the position).

### Headline ANOVA results

| Position | Projections 2026 (F, p, η²) | Actual 2025 (F, p, η²) |
|---|---|---|
| QB | F=3.18, p<0.0001, η²=0.160 | F=2.34, p=0.0001, η²=0.109 |
| RB | F=9.76, p<0.0001, η²=0.131 | F=1.34, p=0.100 (not significant), η²=0.032 |
| WR | F=15.65, p<0.0001, η²=0.131 | F=2.03, p=0.0007, η²=0.033 |
| TE | F=11.55, p<0.0001, η²=0.157 | F=1.30, p=0.127 (not significant), η²=0.040 |

**Takeaway:** Sleeper's own 2026 preseason projections bake in a *stronger*
and more statistically confident opponent effect than what actually played
out historically in 2025 for RB/WR/TE — likely because projections are
smoothed/systematic (same opponent adjustment applied consistently across
players and weeks), whereas real-world results are much noisier (injuries,
game script, weather, garbage time, etc.), which dilutes the ANOVA signal
even when the raw point swings between best/worst matchups are larger. QB is
the one position where both projections and actual results agree there's a
real, sizable opponent effect.

**Biggest raw swing (best matchup avg_residual − worst matchup avg_residual):**

| Position | Projections 2026 swing | Actual 2025 swing |
|---|---|---|
| QB | ~1.02 pts (DAL +0.40 vs LV −0.61) | ~10.24 pts (DAL +4.93 vs MIN −5.32) |
| RB | ~0.36 pts (NYG +0.19 vs BAL −0.17) | ~3.92 pts (CIN +2.17 vs SEA −1.75) |
| WR | ~0.18 pts (WAS +0.09 vs DEN −0.09) | ~4.20 pts (DAL +2.24 vs MIN −1.96) |
| TE | ~0.13 pts (WAS +0.05 vs DEN −0.07) | ~3.83 pts (CIN +2.44 vs BUF −1.39) |

This confirms the hypothesis: **actual historical point swings between the
best and worst matchups are 4–10x larger than what forward projections
currently bake in**, especially at RB/WR/TE.

---

## 1. Projections (2026 season, weeks 1–18)

Sorted by `avg_residual` ascending (toughest matchup first) within each
position. `avg_projected_points` is the raw average; `avg_residual` is the
player-centered adjustment; `n_games` is the number of player-week rows
behind the average.

### QB

| Opponent | n_games | Avg Pts | Avg Residual | Residual Z |
|---|---|---|---|---|
| LV | 18 | 18.71 | -0.613 | -0.87 |
| DEN | 17 | 18.68 | -0.452 | -7.61 |
| BAL | 18 | 18.51 | -0.393 | -6.47 |
| MIN | 17 | 19.75 | -0.255 | -24.44 |
| PHI | 17 | 19.64 | -0.246 | -10.87 |
| SEA | 17 | 19.78 | -0.224 | -6.07 |
| NE | 17 | 19.28 | -0.189 | -12.48 |
| KC | 17 | 19.30 | -0.100 | -10.20 |
| PIT | 17 | 19.40 | -0.095 | -1.20 |
| LAC | 16 | 19.67 | -0.094 | -6.66 |
| BUF | 17 | 19.32 | -0.091 | -2.24 |
| SF | 17 | 19.54 | -0.070 | -3.36 |
| LAR | 17 | 20.16 | -0.061 | -1.11 |
| NYJ | 17 | 19.52 | -0.053 | -1.15 |
| NO | 17 | 19.05 | -0.032 | -0.48 |
| DET | 17 | 19.53 | -0.024 | -1.63 |
| NYG | 18 | 19.66 | -0.002 | -0.04 |
| GB | 17 | 19.84 | +0.001 | 0.12 |
| CLE | 17 | 19.25 | +0.015 | 1.96 |
| HOU | 17 | 19.81 | +0.023 | 0.48 |
| JAX | 17 | 19.73 | +0.083 | 0.77 |
| TEN | 17 | 19.47 | +0.107 | 2.02 |
| TB | 17 | 19.62 | +0.164 | 3.55 |
| CAR | 17 | 19.96 | +0.195 | 4.52 |
| ARI | 17 | 20.31 | +0.205 | 13.66 |
| IND | 18 | 19.67 | +0.214 | 3.40 |
| ATL | 18 | 19.76 | +0.232 | 5.39 |
| CHI | 17 | 20.09 | +0.277 | 18.62 |
| CIN | 19 | 18.89 | +0.335 | 6.24 |
| MIA | 16 | 20.70 | +0.373 | 15.72 |
| WAS | 17 | 20.61 | +0.377 | 20.08 |
| DAL | 17 | 20.63 | +0.404 | 20.43 |

### RB

| Opponent | n_games | Avg Pts | Avg Residual | Residual Z |
|---|---|---|---|---|
| BAL | 62 | 5.43 | -0.167 | -7.78 |
| LAR | 64 | 5.18 | -0.141 | -6.66 |
| PIT | 66 | 5.02 | -0.126 | -7.53 |
| SEA | 61 | 5.50 | -0.122 | -3.16 |
| TB | 60 | 5.64 | -0.104 | -6.97 |
| NE | 63 | 5.37 | -0.103 | -3.02 |
| TEN | 64 | 5.20 | -0.089 | -6.91 |
| NYJ | 61 | 5.59 | -0.074 | -6.19 |
| DEN | 62 | 5.46 | -0.065 | -3.52 |
| PHI | 66 | 5.00 | -0.065 | -6.94 |
| SF | 65 | 5.11 | -0.062 | -6.17 |
| GB | 59 | 5.97 | -0.029 | -3.93 |
| NO | 55 | 6.23 | -0.026 | -2.12 |
| KC | 62 | 5.64 | -0.016 | -2.17 |
| HOU | 70 | 4.81 | +0.009 | 0.79 |
| IND | 71 | 4.66 | +0.009 | 0.99 |
| ATL | 61 | 5.62 | +0.013 | 1.41 |
| JAX | 66 | 5.08 | +0.015 | 1.47 |
| WAS | 68 | 4.98 | +0.016 | 1.80 |
| MIN | 59 | 5.89 | +0.039 | 0.34 |
| LV | 69 | 5.09 | +0.043 | 4.64 |
| ARI | 69 | 5.00 | +0.047 | 3.63 |
| CIN | 64 | 5.41 | +0.059 | 6.38 |
| LAC | 64 | 5.42 | +0.059 | 1.14 |
| DET | 63 | 5.49 | +0.064 | 1.64 |
| DAL | 69 | 4.97 | +0.070 | 4.96 |
| CHI | 65 | 5.45 | +0.070 | 3.60 |
| MIA | 62 | 5.79 | +0.071 | 2.48 |
| CLE | 63 | 5.56 | +0.090 | 6.70 |
| BUF | 63 | 5.60 | +0.121 | 6.03 |
| CAR | 64 | 5.64 | +0.163 | 5.38 |
| NYG | 67 | 5.15 | +0.193 | 4.58 |

### WR

| Opponent | n_games | Avg Pts | Avg Residual | Residual Z |
|---|---|---|---|---|
| DEN | 104 | 4.25 | -0.092 | -7.66 |
| BAL | 99 | 4.74 | -0.082 | -2.55 |
| PHI | 101 | 4.89 | -0.061 | -4.99 |
| BUF | 98 | 4.72 | -0.051 | -4.95 |
| PIT | 107 | 4.51 | -0.046 | -3.53 |
| SEA | 106 | 4.49 | -0.046 | -5.46 |
| MIN | 101 | 4.84 | -0.039 | -8.06 |
| NE | 97 | 4.73 | -0.037 | -2.96 |
| NO | 96 | 4.82 | -0.026 | -7.28 |
| SF | 101 | 4.75 | -0.025 | -2.04 |
| LAC | 107 | 4.35 | -0.024 | -6.61 |
| KC | 106 | 4.38 | -0.022 | -1.00 |
| HOU | 106 | 4.60 | -0.021 | -1.62 |
| CLE | 101 | 4.68 | -0.016 | -2.47 |
| NYG | 105 | 4.85 | -0.009 | -1.82 |
| LAR | 108 | 4.54 | -0.007 | -0.71 |
| GB | 93 | 5.37 | -0.003 | -1.26 |
| DET | 94 | 4.98 | +0.003 | 0.76 |
| JAX | 103 | 4.76 | +0.004 | 0.38 |
| NYJ | 101 | 4.55 | +0.005 | 0.77 |
| IND | 100 | 4.84 | +0.012 | 0.61 |
| CAR | 97 | 5.22 | +0.023 | 4.50 |
| ATL | 103 | 4.84 | +0.029 | 7.36 |
| LV | 102 | 4.59 | +0.038 | 2.99 |
| ARI | 106 | 4.84 | +0.038 | 7.58 |
| TEN | 105 | 4.62 | +0.044 | 3.76 |
| CIN | 102 | 4.51 | +0.048 | 8.44 |
| CHI | 95 | 5.39 | +0.048 | 7.36 |
| TB | 92 | 5.52 | +0.065 | 4.74 |
| MIA | 102 | 4.84 | +0.071 | 8.90 |
| DAL | 108 | 4.62 | +0.088 | 8.25 |
| WAS | 99 | 5.23 | +0.093 | 5.30 |

### TE

| Opponent | n_games | Avg Pts | Avg Residual | Residual Z |
|---|---|---|---|---|
| DEN | 57 | 3.40 | -0.074 | -5.43 |
| BUF | 57 | 3.22 | -0.038 | -6.03 |
| PHI | 61 | 3.12 | -0.037 | -7.17 |
| BAL | 66 | 2.70 | -0.035 | -7.23 |
| PIT | 66 | 2.66 | -0.031 | -4.20 |
| MIN | 56 | 3.51 | -0.031 | -1.10 |
| SEA | 61 | 3.31 | -0.027 | -4.39 |
| LAR | 59 | 3.42 | -0.025 | -1.01 |
| LAC | 61 | 3.17 | -0.017 | -5.95 |
| NE | 59 | 3.21 | -0.015 | -6.22 |
| NO | 65 | 2.99 | -0.014 | -5.37 |
| KC | 60 | 3.15 | -0.014 | -5.73 |
| CLE | 67 | 2.72 | -0.011 | -5.39 |
| NYG | 61 | 3.14 | -0.008 | -2.59 |
| HOU | 61 | 3.04 | -0.008 | -1.58 |
| NYJ | 55 | 3.62 | -0.007 | -0.62 |
| GB | 62 | 3.05 | -0.002 | -1.74 |
| SF | 62 | 3.17 | +0.004 | 0.52 |
| DET | 59 | 3.35 | +0.008 | 1.07 |
| JAX | 63 | 2.99 | +0.009 | 0.77 |
| CAR | 65 | 2.89 | +0.010 | 3.93 |
| LV | 55 | 3.37 | +0.019 | 4.45 |
| ATL | 62 | 3.04 | +0.020 | 4.71 |
| IND | 64 | 2.83 | +0.021 | 2.62 |
| TB | 65 | 3.02 | +0.027 | 6.55 |
| ARI | 60 | 3.22 | +0.027 | 5.34 |
| CHI | 62 | 2.98 | +0.027 | 5.67 |
| TEN | 65 | 2.93 | +0.028 | 5.78 |
| CIN | 60 | 3.03 | +0.030 | 6.64 |
| MIA | 57 | 3.46 | +0.048 | 6.00 |
| DAL | 63 | 3.07 | +0.054 | 6.25 |
| WAS | 63 | 3.06 | +0.055 | 7.06 |

---

## 2. Actual Results (2025 season, weeks 1–18)

Sorted by `avg_residual` ascending (toughest matchup first) within each
position.

### QB

| Opponent | n_games | Avg Pts | Avg Residual | Residual Z |
|---|---|---|---|---|
| MIN | 18 | 10.74 | -5.315 | -2.86 |
| CAR | 23 | 10.86 | -4.521 | -3.50 |
| CLE | 22 | 10.80 | -3.763 | -2.66 |
| LAC | 21 | 10.99 | -3.298 | -3.12 |
| HOU | 17 | 13.73 | -2.090 | -1.16 |
| PHI | 18 | 14.39 | -1.935 | -1.33 |
| KC | 20 | 13.08 | -1.732 | -1.22 |
| BUF | 20 | 11.73 | -1.527 | -1.06 |
| LV | 20 | 13.74 | -0.939 | -0.87 |
| DEN | 20 | 12.57 | -0.793 | -0.46 |
| SEA | 18 | 14.22 | -0.704 | -0.46 |
| NO | 18 | 13.75 | -0.672 | -0.42 |
| NE | 20 | 13.00 | -0.338 | -0.27 |
| GB | 17 | 14.72 | -0.299 | -0.18 |
| IND | 20 | 14.99 | -0.295 | -0.22 |
| JAX | 22 | 13.17 | -0.227 | -0.13 |
| ARI | 21 | 14.22 | -0.126 | -0.10 |
| BAL | 21 | 14.87 | +0.131 | 0.07 |
| NYG | 22 | 14.49 | +0.248 | 0.16 |
| MIA | 21 | 15.13 | +0.324 | 0.22 |
| LAR | 19 | 14.41 | +0.651 | 0.43 |
| ATL | 18 | 16.30 | +0.943 | 0.51 |
| CIN | 20 | 16.22 | +1.438 | 0.89 |
| SF | 18 | 16.49 | +1.466 | 1.07 |
| TEN | 22 | 15.00 | +1.865 | 1.37 |
| DET | 17 | 16.93 | +2.140 | 1.52 |
| CHI | 20 | 16.56 | +2.707 | 1.39 |
| NYJ | 21 | 16.16 | +2.828 | 1.37 |
| TB | 19 | 18.06 | +3.255 | 1.72 |
| WAS | 19 | 17.90 | +3.407 | 2.78 |
| PIT | 17 | 19.68 | +3.612 | 2.47 |
| DAL | 19 | 21.20 | +4.926 | 2.56 |

### RB

| Opponent | n_games | Avg Pts | Avg Residual | Residual Z |
|---|---|---|---|---|
| SEA | 40 | 6.68 | -1.754 | -2.19 |
| PIT | 42 | 6.94 | -1.349 | -1.77 |
| HOU | 41 | 7.40 | -1.038 | -1.20 |
| NE | 41 | 6.79 | -1.034 | -1.38 |
| KC | 40 | 7.29 | -0.963 | -0.96 |
| NO | 36 | 8.88 | -0.953 | -1.22 |
| LAC | 43 | 6.60 | -0.926 | -1.07 |
| JAX | 38 | 7.15 | -0.895 | -1.10 |
| DEN | 42 | 6.31 | -0.876 | -1.39 |
| CLE | 39 | 8.49 | -0.840 | -0.91 |
| LAR | 38 | 7.98 | -0.747 | -0.75 |
| MIN | 39 | 7.71 | -0.614 | -0.56 |
| DET | 38 | 7.81 | -0.589 | -0.72 |
| IND | 40 | 7.37 | -0.534 | -0.66 |
| CHI | 39 | 8.36 | -0.287 | -0.36 |
| PHI | 42 | 8.44 | -0.241 | -0.30 |
| CAR | 41 | 8.85 | -0.223 | -0.27 |
| BAL | 42 | 8.25 | -0.210 | -0.25 |
| GB | 37 | 8.58 | -0.181 | -0.15 |
| ATL | 42 | 7.85 | -0.145 | -0.15 |
| TB | 36 | 9.34 | +0.039 | 0.04 |
| TEN | 43 | 7.88 | +0.260 | 0.29 |
| MIA | 45 | 8.50 | +0.666 | 0.60 |
| SF | 39 | 9.02 | +0.672 | 0.92 |
| LV | 43 | 8.07 | +0.765 | 0.93 |
| WAS | 44 | 8.99 | +1.105 | 1.21 |
| BUF | 43 | 8.83 | +1.147 | 1.02 |
| ARI | 41 | 10.05 | +1.257 | 1.48 |
| DAL | 41 | 9.46 | +1.636 | 1.60 |
| NYG | 37 | 10.26 | +2.065 | 1.68 |
| NYJ | 41 | 10.76 | +2.101 | 1.73 |
| CIN | 44 | 9.80 | +2.167 | 2.11 |

### WR

| Opponent | n_games | Avg Pts | Avg Residual | Residual Z |
|---|---|---|---|---|
| MIN | 48 | 6.69 | -1.957 | -2.25 |
| DEN | 61 | 5.85 | -1.532 | -2.63 |
| HOU | 58 | 6.07 | -1.451 | -2.70 |
| PHI | 56 | 6.39 | -1.446 | -2.33 |
| KC | 63 | 6.03 | -1.290 | -2.59 |
| CAR | 59 | 6.34 | -1.222 | -2.06 |
| ARI | 58 | 7.17 | -0.822 | -1.21 |
| SEA | 54 | 6.64 | -0.669 | -0.79 |
| LAC | 55 | 6.75 | -0.626 | -0.96 |
| NO | 56 | 6.83 | -0.402 | -0.63 |
| LV | 67 | 6.88 | -0.385 | -0.88 |
| BUF | 53 | 6.91 | -0.322 | -0.47 |
| CIN | 57 | 6.33 | -0.035 | -0.05 |
| MIA | 64 | 6.32 | -0.009 | -0.02 |
| GB | 57 | 7.45 | +0.028 | 0.04 |
| TB | 59 | 7.26 | +0.030 | 0.05 |
| SF | 58 | 7.58 | +0.030 | 0.04 |
| JAX | 59 | 7.14 | +0.235 | 0.41 |
| TEN | 64 | 7.82 | +0.288 | 0.49 |
| NYG | 63 | 7.50 | +0.354 | 0.58 |
| WAS | 59 | 8.26 | +0.356 | 0.51 |
| CLE | 53 | 7.16 | +0.367 | 0.57 |
| PIT | 62 | 7.88 | +0.389 | 0.57 |
| LAR | 57 | 7.95 | +0.617 | 0.76 |
| NYJ | 52 | 7.93 | +0.802 | 1.05 |
| IND | 62 | 8.05 | +0.809 | 1.27 |
| BAL | 69 | 7.22 | +0.872 | 1.44 |
| CHI | 62 | 8.31 | +0.890 | 1.25 |
| DET | 60 | 8.47 | +0.960 | 1.20 |
| ATL | 59 | 7.78 | +1.080 | 1.53 |
| NE | 55 | 7.37 | +1.302 | 1.99 |
| DAL | 59 | 9.45 | +2.243 | 2.92 |

### TE

| Opponent | n_games | Avg Pts | Avg Residual | Residual Z |
|---|---|---|---|---|
| BUF | 24 | 4.40 | -1.388 | -1.51 |
| PHI | 25 | 4.33 | -1.169 | -1.60 |
| BAL | 34 | 4.44 | -1.076 | -2.25 |
| NO | 32 | 5.00 | -1.052 | -1.57 |
| HOU | 33 | 4.95 | -0.940 | -1.26 |
| LAC | 27 | 5.20 | -0.823 | -1.31 |
| CLE | 38 | 4.57 | -0.773 | -1.30 |
| MIN | 30 | 4.73 | -0.695 | -0.85 |
| KC | 32 | 4.50 | -0.605 | -1.00 |
| NYG | 30 | 5.07 | -0.542 | -0.69 |
| CAR | 33 | 5.69 | -0.530 | -0.68 |
| LV | 27 | 5.06 | -0.496 | -0.73 |
| ATL | 26 | 5.01 | -0.478 | -0.67 |
| GB | 30 | 5.22 | -0.463 | -0.75 |
| SEA | 31 | 6.41 | -0.297 | -0.44 |
| DAL | 29 | 5.62 | -0.211 | -0.33 |
| LAR | 27 | 6.25 | -0.104 | -0.14 |
| SF | 32 | 6.27 | -0.076 | -0.11 |
| CHI | 30 | 5.77 | +0.111 | 0.17 |
| DET | 34 | 5.54 | +0.200 | 0.23 |
| NE | 31 | 5.80 | +0.273 | 0.35 |
| NYJ | 34 | 6.08 | +0.303 | 0.41 |
| DEN | 31 | 5.85 | +0.335 | 0.44 |
| JAX | 31 | 6.32 | +0.336 | 0.34 |
| IND | 33 | 6.16 | +0.391 | 0.49 |
| TEN | 27 | 7.03 | +0.396 | 0.44 |
| MIA | 36 | 6.23 | +0.860 | 1.47 |
| TB | 31 | 7.03 | +1.021 | 0.80 |
| PIT | 34 | 6.53 | +1.146 | 1.27 |
| ARI | 32 | 7.28 | +1.390 | 1.83 |
| WAS | 31 | 7.33 | +1.446 | 1.69 |
| CIN | 35 | 8.49 | +2.443 | 2.51 |

---

## Notes / caveats

- `n_games` for the actual-results run counts individual player-week rows
  (not unique games) where a player at that position actually recorded
  playing time against that opponent, aggregated across the full 2025
  season.
- Projections numbers reflect Sleeper's current (pre-2026-season) weekly
  projections and are subject to change as the season approaches and rosters
  are finalized.
- These are single-season historical results for actual data — real opponent
  "defense vs. position" strength can and does shift year over year (coaching
  changes, personnel changes, scheme changes), so the 2025 actual table
  should be treated as one noisy data point, not a guaranteed predictor of
  2026 matchups.
- Raw CSV data backing these tables: `output/opponent_matchup_effects.csv`
  (projections) and `output/opponent_matchup_effects_actual_2025.csv`
  (actual).
