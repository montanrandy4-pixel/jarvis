--[[
	Monetization
	Every gamepass, developer product and timed boost in one table. Systems read
	the bonuses from here rather than hard-coding multipliers, so adding a new
	pass is a config edit, not a code change.

	FILL IN THE Id FIELDS with real asset ids before publishing. An Id of 0 is
	treated as "not configured" and is skipped everywhere.
]]

local Monetization = {}

-- Gamepasses -----------------------------------------------------------------
-- CashBonus is ADDITIVE: base 1.0 + 1.0 (2x Cash) + 0.5 (VIP) = 2.5x.
Monetization.Gamepasses = {
	DoubleCash = {
		Id = 0,
		DisplayName = "2x Cash",
		Description = "Every drop is worth double, forever.",
		CashBonus = 1.0,
		Price = 199,
	},
	AutoCollector = {
		Id = 0,
		DisplayName = "Auto Collector",
		Description = "Your tycoon collects itself while you build.",
		AutoCollectInterval = 5,
		Price = 349,
	},
	VIP = {
		Id = 0,
		DisplayName = "VIP",
		Description = "VIP lounge, +50% cash, golden name tag.",
		CashBonus = 0.5,
		WalkSpeedBonus = 4,
		VipArea = true,
		Price = 499,
	},
	LuckyDrops = {
		Id = 0,
		DisplayName = "Lucky Drops",
		Description = "Droppers roll for rare high-value products.",
		LuckMultiplier = 2,
		Price = 249,
	},
}

-- Developer products ---------------------------------------------------------
Monetization.Products = {
	Cash1k = { Id = 0, DisplayName = "1,000 Cash", Grant = { Type = "Cash", Amount = 1000 }, Price = 25 },
	Cash10k = { Id = 0, DisplayName = "10,000 Cash", Grant = { Type = "Cash", Amount = 10000 }, Price = 99 },
	Cash100k = { Id = 0, DisplayName = "100,000 Cash", Grant = { Type = "Cash", Amount = 100000 }, Price = 499 },
	CashBoost10m = { Id = 0, DisplayName = "2x Cash (10 min)", Grant = { Type = "Boost", Boost = "Cash2x" }, Price = 49 },
	SpeedBoost10m = { Id = 0, DisplayName = "Speed Boost (10 min)", Grant = { Type = "Boost", Boost = "Speed" }, Price = 29 },
}

-- Timed boosts ---------------------------------------------------------------
Monetization.Boosts = {
	Cash2x = { DisplayName = "2x Cash Boost", Duration = 600, CashBonus = 1.0 },
	Speed = { DisplayName = "Speed Boost", Duration = 600, WalkSpeedBonus = 8 },
}

-- Lookups --------------------------------------------------------------------
local function indexById(source, kind)
	local byId = {}
	for key, entry in source do
		entry.Key = key
		entry.Kind = kind
		if entry.Id and entry.Id ~= 0 then
			byId[entry.Id] = entry
		end
	end
	return byId
end

local gamepassesById = indexById(Monetization.Gamepasses, "Gamepass")
local productsById = indexById(Monetization.Products, "Product")

for key, boost in Monetization.Boosts do
	boost.Key = key
end

function Monetization.GetGamepassById(id)
	return gamepassesById[id]
end

function Monetization.GetProductById(id)
	return productsById[id]
end

function Monetization.IsConfigured(entry)
	return entry ~= nil and entry.Id ~= nil and entry.Id ~= 0
end

return Monetization
