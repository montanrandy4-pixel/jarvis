--[[
	MonetizationService
	Gamepass ownership cache + the game's single ProcessReceipt handler.

	Receipt safety:
		* A purchase is granted at most once, ever - PurchaseId is recorded in the
		  player's save and checked before granting.
		* PurchaseGranted is only returned after the save has been written. If the
		  write fails we return NotProcessedYet and Roblox retries, so a player can
		  never pay and lose the item.

	Extending:
		MonetizationService:RegisterGrant("Pet", function(player, product, receipt)
			... return true on success
		end)
]]

local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local GameConfig = require(Shared.Config.GameConfig)
local Monetization = require(Shared.Config.Monetization)
local Net = require(Shared.Util.Net)
local Signal = require(Shared.Util.Signal)
local TableUtil = require(Shared.Util.TableUtil)

local OWNERSHIP_RETRY = 30 -- seconds before retrying a failed ownership check

local MonetizationService = {
	Name = "MonetizationService",
	Priority = 90, -- after DataService, before gameplay services
}

function MonetizationService:Init()
	self._ownership = {} -- [player] = { [passKey] = boolean }
	self._ownershipFailedAt = {} -- [player] = { [passKey] = os.clock() }
	self._grantHandlers = {}

	self.PassGranted = Signal.new() -- (player, passKey)
	self.BoostGranted = Signal.new() -- (player, boostKey, expiry)
	self.ProductPurchased = Signal.new() -- (player, product, receiptInfo)

	self._promptRemote = Net.Event("Monetization/Prompt")
	self._promptLimiter = Net.RateLimiter(10, 5)

	self:RegisterGrant("Cash", function(player, product)
		-- Paid cash is never multiplied: the player already paid for the amount.
		self.Services.EconomyService:AddCash(player, product.Grant.Amount, {
			Multiplied = false,
			Reason = "Product:" .. product.Key,
		})
		return true
	end)

	self:RegisterGrant("Boost", function(player, product)
		return self:GrantBoost(player, product.Grant.Boost)
	end)
end

function MonetizationService:Start()
	MarketplaceService.ProcessReceipt = function(receiptInfo)
		local ok, decision = pcall(function()
			return self:_processReceipt(receiptInfo)
		end)
		if not ok then
			warn("[MonetizationService] ProcessReceipt errored: " .. tostring(decision))
			return Enum.ProductPurchaseDecision.NotProcessedYet
		end
		return decision
	end

	MarketplaceService.PromptGamePassPurchaseFinished:Connect(function(player, passId, wasPurchased)
		if not wasPurchased then
			return
		end
		local pass = Monetization.GetGamepassById(passId)
		if not pass then
			return
		end
		local cache = self._ownership[player]
		if cache then
			cache[pass.Key] = true
		end
		self.PassGranted:Fire(player, pass.Key)
	end)

	-- Client-driven prompts, rate limited so a exploiter cannot spam the API.
	self._promptRemote.OnServerEvent:Connect(function(player, kind, key)
		if not self._promptLimiter(player) then
			return
		end
		if kind == "Gamepass" then
			self:PromptPass(player, key)
		elseif kind == "Product" then
			self:PromptProduct(player, key)
		end
	end)

	Players.PlayerRemoving:Connect(function(player)
		self._ownership[player] = nil
		self._ownershipFailedAt[player] = nil
	end)

	self.Services.DataService.ProfileLoaded:Connect(function(player)
		task.spawn(function()
			self:_prefetchOwnership(player)
		end)
	end)
end

-- Ownership ------------------------------------------------------------------
function MonetizationService:_prefetchOwnership(player)
	for key in Monetization.Gamepasses do
		self:OwnsPass(player, key)
	end
end

--[[ Cached, non-yielding after the first successful check. A failed API call is
	 not cached as false - it is retried, so an outage never silently revokes a
	 pass a player paid for. ]]
function MonetizationService:OwnsPass(player, passKey)
	local pass = Monetization.Gamepasses[passKey]
	if not Monetization.IsConfigured(pass) then
		return false
	end

	local cache = self._ownership[player]
	if not cache then
		cache = {}
		self._ownership[player] = cache
	end
	if cache[passKey] ~= nil then
		return cache[passKey]
	end

	local failures = self._ownershipFailedAt[player]
	if failures and failures[passKey] and os.clock() - failures[passKey] < OWNERSHIP_RETRY then
		return false
	end

	local ok, owns = pcall(function()
		return MarketplaceService:UserOwnsGamePassAsync(player.UserId, pass.Id)
	end)

	if ok then
		cache[passKey] = owns
		return owns
	end

	warn(string.format("[MonetizationService] ownership check failed for %s/%s: %s", player.Name, passKey, tostring(owns)))
	failures = failures or {}
	failures[passKey] = os.clock()
	self._ownershipFailedAt[player] = failures
	return false
end

function MonetizationService:PromptPass(player, passKey)
	local pass = Monetization.Gamepasses[passKey]
	if not Monetization.IsConfigured(pass) then
		warn(string.format("[MonetizationService] gamepass %q has no Id set", tostring(passKey)))
		return
	end
	pcall(function()
		MarketplaceService:PromptGamePassPurchase(player, pass.Id)
	end)
end

function MonetizationService:PromptProduct(player, productKey)
	local product = Monetization.Products[productKey]
	if not Monetization.IsConfigured(product) then
		warn(string.format("[MonetizationService] product %q has no Id set", tostring(productKey)))
		return
	end
	pcall(function()
		MarketplaceService:PromptProductPurchase(player, product.Id)
	end)
end

-- Boosts ---------------------------------------------------------------------
function MonetizationService:GrantBoost(player, boostKey)
	local boost = Monetization.Boosts[boostKey]
	if not boost then
		return false
	end

	local dataService = self.Services.DataService
	local data = dataService:Get(player)
	if not data then
		return false
	end

	-- Stack duration rather than overwrite, so a double purchase is never eaten.
	local now = os.time()
	local current = data.Boosts[boostKey]
	local base = if type(current) == "number" and current > now then current else now
	local expiry = base + boost.Duration

	dataService:Set(player, "Boosts." .. boostKey, expiry)
	self.BoostGranted:Fire(player, boostKey, expiry)
	return true
end

-- Receipts -------------------------------------------------------------------
function MonetizationService:RegisterGrant(grantType, handler)
	self._grantHandlers[grantType] = handler
end

function MonetizationService:_processReceipt(receiptInfo)
	local Decision = Enum.ProductPurchaseDecision
	local player = Players:GetPlayerByUserId(receiptInfo.PlayerId)
	if not player then
		return Decision.NotProcessedYet -- retried when they rejoin
	end

	local dataService = self.Services.DataService
	local data = dataService:Get(player)
	if not data then
		return Decision.NotProcessedYet -- profile still loading
	end

	local purchaseId = tostring(receiptInfo.PurchaseId)

	-- Already granted in a previous attempt: just make sure it is persisted.
	if data.Purchases.Receipts[purchaseId] then
		if dataService:SaveAsync(player) then
			return Decision.PurchaseGranted
		end
		return Decision.NotProcessedYet
	end

	local product = Monetization.GetProductById(receiptInfo.ProductId)
	if not product then
		warn(string.format("[MonetizationService] unknown product id %d - not granting", receiptInfo.ProductId))
		return Decision.NotProcessedYet -- leave it pending until config is fixed
	end

	local handler = self._grantHandlers[product.Grant and product.Grant.Type]
	if not handler then
		warn(string.format("[MonetizationService] no grant handler for %q", tostring(product.Grant and product.Grant.Type)))
		return Decision.NotProcessedYet
	end

	local ok, granted = pcall(handler, player, product, receiptInfo)
	if not ok or granted == false then
		warn(string.format("[MonetizationService] grant failed for %s: %s", product.Key, tostring(granted)))
		return Decision.NotProcessedYet
	end

	data.Purchases.Receipts[purchaseId] = os.time()
	data.Purchases.Counts[product.Key] = (data.Purchases.Counts[product.Key] or 0) + 1
	data.Purchases.Robux += receiptInfo.CurrencySpent or 0
	self:_pruneReceipts(data)

	-- Only confirm the receipt once the grant is on disk.
	if not dataService:SaveAsync(player) then
		return Decision.NotProcessedYet
	end

	self.ProductPurchased:Fire(player, product, receiptInfo)
	return Decision.PurchaseGranted
end

--[[ Receipts are a ring buffer: Roblox only ever retries recent purchases, so
	 keeping the newest N is enough and the save file stays bounded. ]]
function MonetizationService:_pruneReceipts(data)
	local receipts = data.Purchases.Receipts
	local limit = GameConfig.Data.MaxStoredReceipts
	if TableUtil.Count(receipts) <= limit then
		return
	end

	local ordered = {}
	for purchaseId, timestamp in receipts do
		table.insert(ordered, { id = purchaseId, at = timestamp })
	end
	table.sort(ordered, function(a, b)
		return a.at > b.at
	end)
	for index = limit + 1, #ordered do
		receipts[ordered[index].id] = nil
	end
end

return MonetizationService
