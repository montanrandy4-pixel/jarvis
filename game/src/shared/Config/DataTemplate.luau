--[[
	DataTemplate
	The shape of a player's save file, plus the migration ladder.

	Rules:
	  * Never delete a key in place - write a migration instead.
	  * Only keys listed in Replicated are ever sent to the client.
	  * Every value must be JSON-serializable (no Instances, no mixed tables).
]]

local DataTemplate = {}

DataTemplate.Version = 1

DataTemplate.Data = {
	Version = 1,

	-- Currency
	Cash = 0,
	LifetimeCash = 0, -- never spent; drives progression gates and rebirth math
	Gems = 0, -- premium/earned soft currency reserved for Phase 4

	-- Tycoon progression
	Tycoon = {
		Unlocked = {}, -- [buttonId] = true
		Rebirths = 0,
		LastPlotId = 0,
	},

	-- Monetization state
	Purchases = {
		Receipts = {}, -- [purchaseId] = unix time (idempotency guard)
		Counts = {}, -- [productKey] = timesBought
		Robux = 0, -- lifetime spend, for whale segmentation
	},
	Boosts = {}, -- [boostKey] = unix expiry

	-- Retention state
	Rewards = {
		LastDailyClaim = 0,
		DailyStreak = 0,
		PlaytimeRewardIndex = 0,
	},

	-- Telemetry
	Stats = {
		FirstJoin = 0,
		LastJoin = 0,
		Joins = 0,
		Playtime = 0,
	},

	Settings = {
		MusicEnabled = true,
		SfxEnabled = true,
		EffectsEnabled = true,
	},
}

-- Top-level keys mirrored to the owning client. Purchases stays server-only.
DataTemplate.Replicated = {
	Cash = true,
	LifetimeCash = true,
	Gems = true,
	Tycoon = true,
	Boosts = true,
	Rewards = true,
	Stats = true,
	Settings = true,
}

--[[ Migrations[n] upgrades a save from version n to version n + 1.
	 Example:
	 DataTemplate.Migrations = {
	 	[1] = function(data)
	 		data.Gems = math.floor((data.LifetimeCash or 0) / 10000)
	 	end,
	 }
]]
DataTemplate.Migrations = {}

function DataTemplate.Migrate(data)
	local version = tonumber(data.Version) or 1
	while version < DataTemplate.Version do
		local migration = DataTemplate.Migrations[version]
		if migration then
			local ok, err = pcall(migration, data)
			if not ok then
				warn(string.format("[DataTemplate] migration %d failed: %s", version, tostring(err)))
				break
			end
		end
		version += 1
		data.Version = version
	end
	return data
end

return DataTemplate
