# Tycoon — Phase 1: Core Architecture & Data Saving

A Roblox tycoon built service-first: one bootstrapper, one data owner, one
receipt handler. Everything later — droppers, plots, rebirths, shops, quests —
plugs into the seams laid here instead of reaching for a DataStore.

Sync it with [Rojo](https://rojo.space):

```bash
rojo serve default.project.json   # then connect from the Rojo Studio plugin
```

## What Phase 1 ships

| Piece | Where | What it does |
|---|---|---|
| Bootstrapper | `src/server/init.server.luau` | Requires services, injects a registry, `Init` all → `Start` all |
| SessionStore | `src/server/Modules/SessionStore.luau` | Session-locked DataStore wrapper: retries, budget awareness, autosave, lock handover |
| DataService | `src/server/Services/DataService.luau` | Owns every save; dotted-path reads/writes, leaderstats, replication, shutdown flush |
| EconomyService | `src/server/Services/EconomyService.luau` | The only path cash takes in or out, with multipliers applied centrally |
| MonetizationService | `src/server/Services/MonetizationService.luau` | Pass ownership cache + the game's single idempotent `ProcessReceipt` |
| DataController | `src/client/Controllers/DataController.luau` | Read-only client mirror with `Observe(path, fn)` for UI |

## Before you publish

Open `src/shared/Config/Monetization.luau` and fill in the asset ids. An `Id` of
`0` means "not configured" — prompts warn instead of firing and ownership checks
return `false`, so the game runs fine while you are still creating the assets.

```lua
DoubleCash = { Id = 0, DisplayName = "2x Cash", CashBonus = 1.0, Price = 199 },
```

Adding a pass is a config edit, not a code change: anything with a `CashBonus`
is picked up automatically by `EconomyService:GetCashMultiplier`.

## How saving works

One server owns a save at a time. The record carries a `SessionJobId` and a
heartbeat that every write refreshes.

1. A joining server finds a live lock → writes `ForceLoad` asking for it.
2. The owner notices on its next autosave (≤30s) → saves, drops the lock, and
   kicks its now-stale client.
3. The joining server claims it on the next poll.
4. If the owner never answers — crashed server — the lock is stolen after
   ~48s, or immediately if the heartbeat is older than 15 minutes.

That is what stops the classic tycoon dupe: join two servers, spend in both,
let the loser's autosave overwrite the winner's.

Other guarantees worth knowing:

- **Autosave** every 30s, plus a forced write after anything bought with Robux.
- **`BindToClose`** releases every session and waits (25s cap) before the server dies.
- **Template reconciliation** fills in new keys on load, so adding a stat never
  wipes anyone. Removing or reshaping one gets a migration in
  `DataTemplate.Migrations` instead.
- **A JSON-encode check** before each write catches unserializable data in
  Studio rather than in production.
- **Studio without API access** transparently falls back to an in-memory mock
  and warns loudly — the full stack is testable offline.

## How money works

Every earn goes through `EconomyService:AddCash`, which applies the multiplier
once, in one place:

```lua
-- a dropper paying out: multiplied by passes and boosts
EconomyService:AddCash(player, 25, { Multiplied = true, Reason = "Dropper" })

-- a Robux purchase: NOT multiplied, they already paid for the amount
EconomyService:AddCash(player, 10000, { Multiplied = false, Reason = "Product:Cash10k" })
```

Bonuses stack additively — `1.0` base `+ 1.0` (2x Cash) `+ 0.5` (VIP) `= 2.5x`.
Multiplicative stacking compounds badly the moment you sell a third cash pass.

Receipts are granted at most once, ever: the `PurchaseId` is written into the
player's save, and `PurchaseGranted` is only returned *after* that save succeeds.
If the write fails, Roblox retries and the player keeps what they paid for.

New product types register their own grant handler rather than editing the
receipt path:

```lua
MonetizationService:RegisterGrant("Pet", function(player, product, receiptInfo)
	return PetService:Give(player, product.Grant.PetId)
end)
```

## Writing a new service

```lua
local MyService = { Name = "MyService", Priority = 50 }

function MyService:Init()  -- build state and signals; do not call other services
	self.SomethingHappened = Signal.new()
end

function MyService:Start() -- everything else is initialized by now
	self.Services.DataService.ProfileLoaded:Connect(function(player, data)
		-- ...
	end)
end

return MyService
```

Drop it in `src/server/Services/`. Never `require` another service directly —
use `self.Services.Other`, which is how circular requires stay impossible.
Higher `Priority` runs earlier (`DataService` is 100).

## Testing this phase

1. Sync with Rojo, press Play. Console shows `[Bootstrap] N services started`.
2. `leaderstats.Cash` appears; check the client mirror in the dev console:
   `_G` is untouched, so call `DataController:Get("Cash")` from a test script.
3. Run a two-server test (Studio → Test → 2 players, or two Studio instances
   with API access on): joining the second should kick the first with the
   "opened in another server" message within ~30s.
4. Earn, leave, rejoin — cash persists. Turn API access off and confirm the
   mock warning appears instead of a kick.

## Roadmap

| Phase | Scope |
|---|---|
| 1 ✅ | Services, session-locked saving, economy pipeline, receipts |
| 2 | Plot assignment, buy buttons, droppers, collectors — all reading `Tycoon.Unlocked` |
| 3 | Shop UI, pass/product prompts, boost timers on screen |
| 4 | Retention: daily rewards, playtime chests, rebirth with a prestige multiplier |
| 5 | Social: group/friend bonuses, leaderboards, trading-safe value floors |
| 6 | Analytics funnels, A/B-able prices, LiveOps events |
