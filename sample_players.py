import pandas as pd

# Load the data
df = pd.read_csv("data/fantasy_half_ppr.csv")

# Get all unique seasons for context
seasons = sorted(df["season"].unique())
print(f"Available seasons: {seasons}")
print()

# Prompt user to filter by season (optional)
season_input = input(f"Enter a season to filter by (or press Enter for all seasons): ").strip()
if season_input:
    df = df[df["season"] == int(season_input)]

# Randomly sample 3 players from each position
positions = df["position"].unique()

print("\n--- Random Sample: 3 Players Per Position ---\n")
for pos in sorted(positions):
    pos_df = df[df["position"] == pos]
    sample = pos_df.sample(n=min(3, len(pos_df)), random_state=None)
    print(f"Position: {pos}")
    print(sample[["player_display_name", "season", "half_ppr_points"]].to_string(index=False))
    print()
