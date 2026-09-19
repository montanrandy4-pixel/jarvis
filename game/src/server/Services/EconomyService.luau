--[[
	EconomyService
	The only way cash enters or leaves a player's save. Every earn routes through
	GetCashMultiplier, so a new gamepass or boost changes payouts everywhere at
	once - droppers, quests, daily rewards - without touching those systems.

		EconomyService:AddCash(player, amount, options)
			options.Multiplied = true   -- apply pass/boost multipliers (earns)
			options.Reason = "Dropper"  -- analytics tag
		EconomyService:TrySpend(player, amount) -> boolean
		EconomyService:GetCashMultiplier(player) -> number
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local GameConfig = require(Shared.Config.GameConfig)
local Monetization = require(Shared.Config.Monetization)
local Signal = require(Shared.Util.Signal)

local ECONOMY = GameConfig.Economy

local EconomyService = {
	Name = "EconomyService",
	Priority = 80,
}

function EconomyService:Init()
	self.CashEarned = Signal.new() -- (player, granted, reason)
	self.CashSpent = Signal.new() -- (player, amount, reason)
	self.MultiplierChanged = Signal.new() -- (player, multiplier)
end

function EconomyService:Start()
	local monetization = self.Services.MonetizationService
	-- Buying a pass or a boost must move the multiplier immediately, not on rejoin.
	monetization.PassGranted:Connect(function(player)
		self.MultiplierChanged:Fire(player, self:GetCashMultiplier(player))
	end)
	monetization.BoostGranted:Connect(function(player)
		self.MultiplierChanged:Fire(player, self:GetCashMultiplier(player))
	end)
end

--[[ Additive stacking: 1.0 base + every owned bonus. Multiplicative stacking
	 compounds badly once you sell three cash passes. ]]
function EconomyService:GetCashMultiplier(player)
	local monetization = self.Services.MonetizationService
	local multiplier = 1

	for key, pass in Monetization.Gamepasses do
		if pass.CashBonus and monetization:OwnsPass(player, key) then
			multiplier += pass.CashBonus
		end
	end

	local data = self.Services.DataService:Get(player)
	if data then
		local now = os.time()
		for key, expiry in data.Boosts do
			local boost = Monetization.Boosts[key]
			if boost and boost.CashBonus and type(expiry) == "number" and expiry > now then
				multiplier += boost.CashBonus
			end
		end
	end

	return multiplier
end

function EconomyService:GetCash(player)
	local data = self.Services.DataService:Get(player)
	return data and data.Cash or 0
end

function EconomyService:AddCash(player, amount, options)
	options = options or {}
	if type(amount) ~= "number" or amount ~= amount or amount <= 0 then
		return 0
	end

	local dataService = self.Services.DataService
	local data = dataService:Get(player)
	if not data then
		return 0
	end

	local granted = amount
	if options.Multiplied ~= false then
		granted = math.floor(amount * self:GetCashMultiplier(player))
	end

	local updated = math.min(data.Cash + granted, ECONOMY.MaxCash)
	granted = updated - data.Cash

	dataService:Set(player, "Cash", updated)
	dataService:Set(player, "LifetimeCash", math.min(data.LifetimeCash + granted, ECONOMY.MaxCash))

	self.CashEarned:Fire(player, granted, options.Reason or "Unknown")
	return granted
end

--[[ Atomic in practice: Luau is single-threaded per server and there is no
	 yield between the check and the write. ]]
function EconomyService:TrySpend(player, amount, reason)
	if type(amount) ~= "number" or amount ~= amount or amount < 0 then
		return false
	end

	local dataService = self.Services.DataService
	local data = dataService:Get(player)
	if not data or data.Cash < amount then
		return false
	end

	dataService:Set(player, "Cash", data.Cash - amount)
	self.CashSpent:Fire(player, amount, reason or "Unknown")
	return true
end

-- "1.25M" style formatting, shared by leaderboards and shop UI later.
function EconomyService.Abbreviate(value)
	local abbreviations = ECONOMY.Abbreviations
	local tier = math.floor(math.log(math.max(value, 1), 1000))
	tier = math.clamp(tier, 0, #abbreviations - 1)
	if tier == 0 then
		return tostring(math.floor(value))
	end
	local scaled = value / 1000 ^ tier
	return string.format("%.2f%s", scaled, abbreviations[tier + 1])
end

return EconomyService
