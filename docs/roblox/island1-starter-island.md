# Island 1 — Windmill Village (Starter Island)

Blueprint for the first island of an anime action RPG in the style of Anime
Legacy. Players spawn here at Level 1 and leave by boat at Level 50.

All coordinates are in studs, with the island's center at the world origin
`(0, 0, 0)`. **+X is east** and **+Z is south**. Sea level is `Y = 0`.

---

## 1. Island layout and Roblox Studio scale

### 1.1 Dimensions

| Measure | Value | Why |
|---|---|---|
| Playable area (ocean included) | 2,048 × 2,048 studs, X and Z from −1,024 to +1,024 | Leaves about 250 studs of ocean around the shore, so flight never reaches the invisible wall abruptly |
| Landmass | ~1,500 (east–west) × 850 (north–south) studs | Long and narrow, so progress runs west to east and the boss is literally "the far end" |
| Spawn to boss distance | ~1,140 studs | About 35 s sprinting, about 19 s flying |
| Sea level | Y = 0 | |
| Beaches | Y = 2–6 | |
| Town plateau | Y = 20 (flat) | Flat ground for shops, spawns and crowds |
| Low-level fields | Y = 15–30 (gentle rolling hills) | Easy fights, nothing to fall off |
| Mid-level highlands | Y = 40–70, cliffs up to Y = 140 | Height tells players this zone is harder |
| Boss mesa | Y = 90 (flat top) | You can see it from town, so it pulls players east |
| Background peak (scenery only) | Y = 160 | Slopes too steep to climb; also hides the arena from the low zone |
| Flight ceiling | Y = 300 | |
| Kill plane | Y = −50 | Sends players back to town |

### 1.2 Terrain style: hybrid (recommended)

- **Smooth Terrain** for ground, cliffs, beaches and water. Use the `Grass`,
  `LeafyGrass`, `Ground`, `Sand`, `Rock`, `Slate` and `Water` materials. You
  get water physics, natural slopes and decent LOD for free.
- **Low-poly MeshParts** for everything built: windmills, houses, fences,
  trees, rocks, crates, the arena and the dock. Flat-shaded, at most 2–3
  colors per asset, `Material = SmoothPlastic`, low `RenderFidelity` on
  far-away props.
- Use a **chunky low-poly color palette** on the terrain too, through
  `Terrain:SetMaterialColor`: saturated greens, warm sandstone, deep teal
  water. That way smooth terrain and low-poly props look like one art style.
- Turn on `Workspace.StreamingEnabled` from day one (`StreamingTargetRadius`
  about 1,024, `StreamingMinRadius` 256). Later islands will need it, and
  adding it afterward breaks scripts that assume parts exist.

**Aesthetic:** a sunny "Windmill Village". It has 3–4 large windmills with
slowly turning blades (animated on the client with a `TweenService` loop or
a `HingeConstraint` motor), wheat fields, wooden fences, stone paths and
cherry-blossom trees in town. The colors get harsher as you go east: golden
fields, then grey quarry rock, then a red-tinted ruined arena under storm
clouds.

### 1.3 Zone map

```
                              N (−Z)
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ OCEAN ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  ~~                                                                ~~
  ~~            [ LOW ZONE ]                 [ ENFORCER PATH ]      ~~
  ~~            Bandit Camp     ^ peak       Lv 35-50   (Q3)  [BOSS]~~
  ~~  [ TOWN ]  Lv 1-15        (scenery)                      [ARENA]~~
  ~~  [ HUB *]======path=====(Q2 outpost)=====cliff trail=====[Lv 50]~~
  ~~  [  Q1  ]                [ MID ZONE ]                          ~~
  ~~   DOCK                   Brute Quarry                          ~~
  ~~                          Lv 15-35                              ~~
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  W (−X)  x = −500            x ≈ 0 … 200               x = 640  E (+X)
                              S (+Z)
```

### 1.4 Sub-zones

| Zone | Center (X, Y, Z) | Footprint | Contents |
|---|---|---|---|
| **Safe Zone / Town Hub** | (−500, 20, 0) | Circle, radius 160 | 4 spawn points around a central fountain; Weapon Shop and Item Shop on the main street; Gacha shrine (a large glowing NPC/statue, visible from spawn); Bank/Storage NPC; Questgiver 1 at the **east gate** (−360, 20, −40); a windmill on the town hill as a landmark. No PvP and no damage. Mobs can't path in. |
| **Dock** (part of the safe zone) | (−640, 4, 220) | 60 × 25 pier | Dock Master NPC and a moored boat (the way to Island 2) |
| **Low-Level Mob Zone — Wheatfield Bandit Camp** | (−130, 22, −140) | 300 × 260 | Open wheat fields, tents and campfires. Bandits (Lv 5) near the town side and Bandit Thugs (Lv 12) deeper in. Gentle slopes and few obstacles, for new players learning to dash. |
| **Quarry Outpost** (small safe camp) | (40, 35, 60) | Circle, radius 30 | Questgiver 2, a campfire and a respawn point. Players don't have to walk back to town. |
| **Mid-Level Mob Zone — Brute Quarry** | (200, 50, 150) | 340 × 280 | Terraced rock pits, mine carts and scaffolding. Brutes (Lv 20) on the upper terraces, Brute Smashers (Lv 30) on the pit floor. Height changes reward double jump. |
| **Enforcer Cliff Path** (approach to the boss) | (470, 75, −80) | 180 × 160 | A narrow cliffside trail with bandit watchtowers and Bandit Enforcers (Lv 40). Questgiver 3 stands at the Cliffside Watchtower (390, 65, 20), which also has a respawn point. |
| **Boss Arena / Dungeon Entrance** | (640, 90, 0) | Circle, radius 85, on the mesa | A ruined stone colosseum. The entrance is a 20-stud-wide gate on the west side. The **Bandit King Raizen** (Lv 50) spawns in the center. On the back (east) wall is a sealed dungeon door, which you can open later for an Island 1 dungeon or raid. |

**Spacing rule:** keep at least **60 studs** of mob-free buffer between the
safe zone and any spawner, and between zones of different levels. Mobs
should leash back at **80 studs** from their spawner, so they never follow
players into town or into the next zone.

### 1.5 Scale and bounds for movement

Reference movement numbers. Tune in one config module (see §3):

| Ability | Value | Distance covered |
|---|---|---|
| WalkSpeed | 24 (Roblox default is 16) | |
| Sprint | 32 | ~1,140 studs in ~36 s |
| Dash | 50 studs over 0.25 s, 2 s cooldown | |
| Jump / double jump | `JumpHeight` 10, double jump adds 10 | ~20 studs of vertical reach |
| Low-tier flight (unlocks at Lv 30 or from an item) | 60 studs/s, hard ceiling at Y = 300 | Spawn to boss in ~19 s |

Map rules that follow from these numbers:

- **Zone sizes of 250–400 studs** take 8–12 s to sprint across. That's large
  enough to feel like a place, and small enough that a quest loop never
  becomes a walking simulator.
- **Spacing within mob packs: 18–25 studs** (more than a dash, less than two).
  A dash moves the player between targets without pulling the whole camp.
- **Cliffs that should block players must be taller than 25 studs**, which
  is above double-jump reach. Ledges meant for parkour should be **at most
  18 studs**.
- **Doorways at least 10 wide × 14 tall. Streets and paths at least 16
  wide.** Dashes need room and the camera must not clip.
- **Arena radius of 85 studs** gives 3–4 dashes of room to kite the boss.
  Don't go below 70 or AoE attacks become impossible to dodge.
- **Boundaries, from softest to hardest:**
  1. Natural blockers (the peak, cliffs over 25 studs, deep water) cover
     most edges.
  2. 250 studs past the shoreline, an invisible wall
     (`CanCollide = true`, `Transparency = 1`, `CanQuery = false`) closes
     the ocean, at X/Z = ±1,024.
  3. A server-side flight clamp at Y = 300. Clamp the position on the
     server; never trust the client.
  4. A kill plane at Y = −50 teleports the player to the nearest
     respawn point.
- Mobs stay in their zone through **leash checks on the server** and
  **pathfinding modifiers** (`PathfindingModifier` with `PassThrough = false`
  around the safe zone), not through walls.

---

## 2. NPCs, quests and level progression

### 2.1 XP curve

```
XP to next level = round(100 × Level^1.5)
```

| Level | 1 | 10 | 15 | 25 | 35 | 45 | 49 |
|---|---|---|---|---|---|---|---|
| XP to next | 100 | 3,162 | 5,809 | 12,500 | 20,706 | 30,187 | 34,300 |

Total XP from Level 1 to 50 is **689,513**. The curve rises steadily
without a wall, and Island 2 can keep the same formula.

### 2.2 Mob roster

| Mob | Level | Zone | Target time to kill (solo, on-level) | Respawn | Max alive per spawner |
|---|---|---|---|---|---|
| Bandit | 5 | Low | 4–5 s | 8 s | 4 |
| Bandit Thug | 12 | Low (deep side) | 5–6 s | 8 s | 4 |
| Brute | 20 | Mid | 6–7 s | 10 s | 4 |
| Brute Smasher | 30 | Mid (pit floor) | 7–8 s | 10 s | 3 |
| Bandit Enforcer (elite) | 40 | Cliff Path | 10–12 s | 15 s | 3 |
| **Bandit King Raizen** (boss) | 50 | Arena | 60–90 s solo, 25–40 s with a party | 180 s | 1 |

Spawners per zone: 4 for Bandits, 3 for Thugs, 4 for Brutes, 3 for
Smashers, 3 for Enforcers. That supports about 12–16 players per server
without fights over mobs.

### 2.3 Quest flow

Quests work like Anime Legacy and Blox Fruits: accept, kill N, turn in, and
repeat. Each questgiver offers **two tiers** and automatically gives the
highest tier the player qualifies for.

**Questgiver 1 — Guard Captain Toma** (town east gate)
- Q1-A *"Bandits at the Gate"*: Lv 1+. Kill 5 Bandits.
- Q1-B *"Thug Problem"*: Lv 8+. Kill 6 Bandit Thugs.
- At Lv 15 he gives a pointer quest to the Quarry Outpost (no fight; 500 XP
  for talking to Questgiver 2).

**Questgiver 2 — Quarry Foreman Gaku** (Quarry Outpost)
- Q2-A *"Brutes in the Pit"*: Lv 15+. Kill 7 Brutes.
- Q2-B *"Smash the Smashers"*: Lv 25+. Kill 8 Brute Smashers.
- At Lv 35 he points the player to the Cliffside Watchtower.

**Questgiver 3 — Ranger Sena** (Cliffside Watchtower)
- Q3-A *"Clear the Cliff Path"*: Lv 35+. Kill 6 Bandit Enforcers.
- Q3-B *"Dethrone the Bandit King"*: Lv 45+. Defeat Bandit King Raizen once.
  The first clear sets `Island1BossDefeated = true`.

### 2.4 Reward balance

| Quest | Level band | Kills | Quest XP | XP per kill | XP per cycle | Coins | Cycles to clear band | Est. time |
|---|---|---|---|---|---|---|---|---|
| Q1-A Bandits | 1 → 8 | 5 | 900 | 40 | 1,100 | 60 | 5.6 | ~4 min |
| Q1-B Bandit Thugs | 8 → 15 | 6 | 3,200 | 120 | 3,920 | 150 | 6.6 | ~7 min |
| Q2-A Brutes | 15 → 25 | 7 | 9,000 | 300 | 11,100 | 350 | 7.8 | ~11 min |
| Q2-B Brute Smashers | 25 → 35 | 8 | 16,000 | 500 | 20,000 | 600 | 8.0 | ~13 min |
| Q3-A Enforcers | 35 → 45 | 6 | 28,000 | 1,000 | 34,000 | 1,000 | 7.3 | ~13 min |
| Q3-B Bandit King | 45 → 50 | 1 | 45,000 | 10,000 | 55,000 | 3,000 | 2.9 | ~6–9 min* |

\* While the boss respawns (180 s), players run Q3-A loops, so the real time
is close to the lower end.

**Totals:** about **55–60 minutes** solo from Level 1 to 50, and about
**25,000 coins** earned on Island 1.

Design notes:
- **About 80% of XP comes from quest turn-ins and about 20% from kills.**
  Players follow the quest path, but grinding without a quest still counts.
- **Each tier is roughly 3× the previous one** in XP per cycle, while cycle
  time grows only about 1.3×. Moving up a tier is clearly worth it.
- **Reduce XP from mobs far below the player's level.** If the player is
  more than 10 levels above the mob, the kill gives 10% XP. This stops
  farming Lv 5 Bandits forever.
- **Boss credit:** every player who dealt at least 10% of the boss's HP
  gets the kill, the quest credit and a drop roll. Damage-share credit
  makes strangers cooperate instead of competing for the last hit.
- **Suggested coin sinks** (they set what ~25k coins means): Iron Katana
  1,500 (around Lv 15), Steel Blade 6,000 (around Lv 30), Gacha spin 1,000,
  extra bank slots 2,500. The boss drop table is 5% for the island's best
  weapon "Raizen's Cleaver", 25% for an accessory, and 100% for 2–4 Gacha
  shards.

### 2.5 Leaving for Island 2

**Recommended: Dock Master + boat.**

- **Old Salt Kaito**, the Dock Master, stands at the town dock (inside the
  safe zone, so departing players are never in danger).
- Requirements, **checked on the server**: `Level >= 50` **and**
  `Island1BossDefeated == true`. Requiring both means a player who was
  power-leveled still has to beat the boss, which is the island's final
  exam.
- Locked state: his dialogue names the missing requirement ("Come back when
  you've reached Level 50" or "Raizen still rules these waters — defeat
  him first").
- Unlocked state: the player sees a "Set Sail" prompt and a 6–8 s boat
  cutscene (camera on a rail, boat tween, fade to black) that hides
  streaming. Then the player is moved to the Island 2 dock spawn and
  `UnlockedIslands.Island2 = true` is saved. After that, both docks offer
  free travel between unlocked islands.
- **Same place or a separate place?** Keep islands 1–3 in **one place**
  with StreamingEnabled and teleport by setting the character's CFrame.
  That's instant, needs no loading screen, and parties stay together.
  Move to `TeleportService` with separate places only when total map size
  or memory needs it.

```lua
-- ServerScriptService/Travel/DockMaster.server.lua
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local IslandConfig = require(ReplicatedStorage.Shared.Config.Islands.Island1)

local dock = workspace.Map.Island1.NPCs.DockMaster
local prompt = dock.HumanoidRootPart.SailPrompt
local destination = workspace.Map.Island2.SpawnLocations.DockArrival

local function missingRequirement(player: Player): string?
	if (player:GetAttribute("Level") or 0) < IslandConfig.ExitLevel then
		return `Come back when you've reached Level {IslandConfig.ExitLevel}.`
	end
	if player:GetAttribute("Island1BossDefeated") ~= true then
		return "Raizen still rules these waters — defeat him first."
	end
	return nil
end

prompt.Triggered:Connect(function(player)
	local reason = missingRequirement(player)
	if reason then
		ReplicatedStorage.Remotes.Travel.Dialogue:FireClient(player, dock.Name, reason)
		return
	end
	ReplicatedStorage.Remotes.Travel.PlayCutscene:FireClient(player, "Island1To2")
	task.wait(IslandConfig.SailCutsceneSeconds)
	local character = player.Character
	if character and character.PrimaryPart then
		character:PivotTo(destination.CFrame + Vector3.new(0, 4, 0))
		player:SetAttribute("UnlockedIsland2", true) -- persisted by the data service
	end
end)
```

---

## 3. Studio setup and folder hierarchy

```
Workspace
├── Map
│   └── Island1
│       ├── Environment
│       │   ├── Buildings          -- shops, houses, windmills (Models)
│       │   ├── Props              -- fences, crates, carts, lamps
│       │   ├── Foliage            -- trees, bushes, wheat
│       │   └── Landmarks          -- windmill hill, fountain, peak rocks
│       ├── SafeZone
│       │   ├── Bounds             -- invisible Part volume, Attribute ZoneType="Safe"
│       │   ├── Fountain
│       │   └── Dock
│       ├── SpawnLocations
│       │   ├── Town_1 … Town_4    -- SpawnLocation, Neutral = true
│       │   ├── QuarryOutpost      -- respawn checkpoint
│       │   └── CliffWatchtower    -- respawn checkpoint
│       ├── Zones                  -- invisible, CanCollide/CanQuery/CanTouch = false
│       │   ├── TownHub            -- Attributes: MinLevel, MaxLevel, Music, DisplayName
│       │   ├── WheatfieldCamp
│       │   ├── QuarryOutpost
│       │   ├── BruteQuarry
│       │   ├── EnforcerCliffPath
│       │   └── BossArena
│       ├── NPCs
│       │   ├── Questgivers
│       │   │   ├── Q1_GuardCaptainToma
│       │   │   ├── Q2_QuarryForemanGaku
│       │   │   └── Q3_RangerSena
│       │   ├── Shops
│       │   │   ├── WeaponShop
│       │   │   └── ItemShop
│       │   ├── GachaShrine
│       │   ├── Bank
│       │   └── DockMaster
│       ├── Spawners               -- anchored invisible Parts; data lives in Attributes
│       │   ├── Low
│       │   │   ├── Bandit_01 … Bandit_04          -- MobId="Bandit", MaxAlive=4, Respawn=8, Radius=40
│       │   │   └── BanditThug_01 … BanditThug_03
│       │   ├── Mid
│       │   │   ├── Brute_01 … Brute_04
│       │   │   └── BruteSmasher_01 … BruteSmasher_03
│       │   └── Elite
│       │       └── BanditEnforcer_01 … BanditEnforcer_03
│       ├── BossArena
│       │   ├── Arena              -- colosseum geometry
│       │   ├── BossSpawn          -- Attributes: MobId="BanditKingRaizen", Respawn=180
│       │   ├── EntranceGate
│       │   ├── ArenaBounds        -- invisible cylinder used for leash and fight-area checks
│       │   └── DungeonDoor        -- sealed for now
│       └── Bounds
│           ├── OceanWalls         -- 4 invisible walls at X/Z = ±1,024
│           └── KillPlane          -- Y = −50
├── Mobs                           -- runtime only: live mob clones go here (empty in Studio)
│   └── Island1
└── Effects                        -- runtime only: temporary VFX, damage numbers

ReplicatedStorage
├── Shared
│   ├── Config
│   │   ├── Movement               -- WalkSpeed, dash, double jump, flight ceiling
│   │   ├── XPCurve                -- XPToNext(level)
│   │   ├── Islands
│   │   │   └── Island1            -- zone level ranges, ExitLevel = 50, spawn names
│   │   ├── Quests
│   │   │   └── Island1Quests
│   │   └── Mobs
│   │       └── Island1Mobs        -- stats, drops, XP, coins (client reads for UI)
│   └── Modules                    -- shared helpers (Signal, Maid/Trove, formatting)
├── Assets
│   ├── NPCRigs                    -- cosmetic NPC models
│   ├── VFX
│   │   └── Island1                -- boss telegraphs, hit sparks
│   ├── Animations
│   │   ├── Mobs
│   │   └── NPCs
│   └── Audio
│       └── Island1                -- zone music, boss theme
└── Remotes
    ├── Quest                      -- AcceptQuest (RF), AbandonQuest (RE), QuestUpdated (RE)
    ├── Shop                       -- Purchase (RF)
    ├── Gacha                      -- Spin (RF)
    ├── Bank                       -- Deposit / Withdraw (RF)
    ├── Combat                     -- UseAbility (RE), DamageNumber (RE)
    └── Travel                     -- Dialogue (RE), PlayCutscene (RE)

ServerStorage                      -- the client never needs these
├── MobTemplates
│   └── Island1                    -- Bandit, BanditThug, Brute, BruteSmasher, BanditEnforcer, BanditKingRaizen
└── DropTables

ServerScriptService
├── Services                       -- DataService, QuestService, MobService, CombatService, ZoneService, TravelService
└── Travel
    └── DockMaster.server.lua
```

Conventions:
- **Mob templates go in ServerStorage, not ReplicatedStorage.** Mobs are
  cloned by the server, and the client gets the copy when it streams into
  Workspace. Clients that don't need the templates can't download them, and
  exploiters can't inspect them.
- **Spawners are data, not scripts.** One `MobService` reads every Part
  tagged `Spawner` with CollectionService and uses its Attributes
  (`MobId`, `MaxAlive`, `Respawn`, `Radius`, `LeashRadius = 80`). Adding a
  camp to Island 2 means copying a Part, not code.
- **Zones are invisible volumes** checked with
  `workspace:GetPartBoundsInBox` or a zone module, for safe-zone damage
  blocking, zone name UI, music and level warnings.
- **Collision groups:** `Players`, `Mobs`, `NPCs`. Players and Mobs don't
  collide with NPCs, and Mobs don't collide with each other, so packs
  don't jam on paths.
- Every Island 2+ folder copies this tree exactly (`Map.Island2`,
  `Island2Quests`, …), so services can loop over islands generically.

### Config module examples

```lua
-- ReplicatedStorage/Shared/Config/XPCurve.lua
local XPCurve = {}

function XPCurve.toNext(level: number): number
	return math.round(100 * level ^ 1.5)
end

-- Reduced XP for mobs far below the player's level
function XPCurve.killMultiplier(playerLevel: number, mobLevel: number): number
	return if playerLevel - mobLevel > 10 then 0.1 else 1
end

return XPCurve
```

```lua
-- ReplicatedStorage/Shared/Config/Quests/Island1Quests.lua
return {
	{ Id = "Q1A", Giver = "Q1_GuardCaptainToma", MinLevel = 1,  Target = "Bandit",           Count = 5, XP = 900,   Coins = 60 },
	{ Id = "Q1B", Giver = "Q1_GuardCaptainToma", MinLevel = 8,  Target = "BanditThug",       Count = 6, XP = 3200,  Coins = 150 },
	{ Id = "Q2A", Giver = "Q2_QuarryForemanGaku", MinLevel = 15, Target = "Brute",            Count = 7, XP = 9000,  Coins = 350 },
	{ Id = "Q2B", Giver = "Q2_QuarryForemanGaku", MinLevel = 25, Target = "BruteSmasher",     Count = 8, XP = 16000, Coins = 600 },
	{ Id = "Q3A", Giver = "Q3_RangerSena",       MinLevel = 35, Target = "BanditEnforcer",   Count = 6, XP = 28000, Coins = 1000 },
	{ Id = "Q3B", Giver = "Q3_RangerSena",       MinLevel = 45, Target = "BanditKingRaizen", Count = 1, XP = 45000, Coins = 3000,
		SetsFlag = "Island1BossDefeated" },
}
```

```lua
-- ReplicatedStorage/Shared/Config/Islands/Island1.lua
return {
	DisplayName = "Windmill Village",
	ExitLevel = 50,
	SailCutsceneSeconds = 7,
	FlightCeilingY = 300,
	KillPlaneY = -50,
	Zones = {
		TownHub           = { MinLevel = 1,  Safe = true },
		WheatfieldCamp    = { MinLevel = 1,  MaxLevel = 15 },
		QuarryOutpost     = { MinLevel = 15, Safe = true },
		BruteQuarry       = { MinLevel = 15, MaxLevel = 35 },
		EnforcerCliffPath = { MinLevel = 35, MaxLevel = 50 },
		BossArena         = { MinLevel = 45, MaxLevel = 50 },
	},
}
```

### Build order

1. Use the Terrain Editor to sculpt the landmass, the town plateau, the quarry
   pits and the boss mesa, using the heights in §1.1. Paint the materials.
2. Build the `Bounds` and `Zones` volumes and the `SpawnLocations`. Walk and
   fly the route with the §1.5 movement values before adding any detail.
3. Block out the town and the arena with grey parts. Check doorways, street
   widths and arena radius.
4. Place the spawners and the three questgivers, then wire up MobService and
   QuestService. Run a full Level 1 → 50 playtest; the target is about 55–60
   minutes.
5. Replace the greybox with low-poly art, using the concept renders in §4
   as reference. Add lighting (`Lighting.Technology = Future`, warm
   `ClockTime` around 14.5, light `Atmosphere` haze) and the turning
   windmills.

---

## 4. Higgsfield concept art prompts

Both prompts are for **16:9**. Use them as reference renders for the art
pass, not as final assets.

### Prompt 1 — Starter Town Hub

> Wide 16:9 isometric three-quarter view of a cheerful low-poly anime
> starter town on a grassy island plateau, styled like a Roblox anime RPG
> game. Flat-shaded chunky geometry, clean solid colors, soft ambient
> occlusion, no textures. A circular stone plaza with a blue fountain at the
> center and four glowing spawn pads with soft cyan light beams around it.
> Wooden and white-plaster shops with colorful cloth awnings line a wide
> cobblestone main street: a weapon shop with crossed swords on its sign, an
> item shop with potion bottles, a bank with a gold coin emblem and a large
> vault door, and a mystical gacha shrine with a giant glowing crystal orb
> and floating sparkles. Chibi anime NPCs with yellow exclamation-mark quest
> markers above their heads stand by each building. A big rustic windmill
> with slowly turning blades on a hill behind the town, pink cherry-blossom
> trees, wooden fences, golden wheat fields on the outskirts, a small
> wooden dock with a sailboat at the edge of a turquoise sea. Bright
> midday sun, fluffy stylized clouds, saturated warm pastel palette, cozy
> and inviting mood, game concept art, high detail, crisp edges, no text,
> no UI.

### Prompt 2 — Boss Arena

> Wide 16:9 cinematic low-angle view of a ruined stone colosseum arena on
> top of a high sea-cliff mesa at the far edge of a low-poly anime island,
> styled like a Roblox anime RPG boss arena. Flat-shaded chunky geometry,
> clean solid colors, no textures. A wide circular sandy arena floor
> cracked with glowing red fissures, broken pillars and crumbling archways
> around it, tattered red bandit banners with a skull-and-crossed-swords
> emblem flapping in the wind. A massive towering bandit king boss stands
> in the center: a muscular anime warlord with a scarred face, a spiked
> shoulder pauldron, a fur cape and a huge jagged cleaver sword, with a red
> aura glowing around him. A tiny anime hero silhouette with a sword stands
> at the arena's west entrance gate for scale. A sealed ancient stone
> dungeon door with faint purple runes on the back wall. Dramatic stormy
> sky with dark purple-red clouds, lightning in the distance, rough ocean
> waves crashing against the cliffs far below, a narrow cliffside path with
> wooden watchtowers leading up to the arena. Moody high-contrast rim
> lighting, epic final-boss atmosphere, game concept art, high detail,
> crisp edges, no text, no UI.

**Optional additions to either prompt:** for a pure top-down layout render,
add *"orthographic top-down view, map layout"*. To keep the art style the
same across both images, give the first render to the second as a style
reference image.
