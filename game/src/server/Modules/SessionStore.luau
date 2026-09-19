--[[
	SessionStore
	Session-locked DataStore wrapper. One player's save is owned by exactly one
	server at a time, so a player bouncing between servers can never duplicate
	cash or roll back progress.

	Record shape stored under each key:
		{
			Data = <the player's save table>,
			Meta = {
				SessionJobId = string?,   -- owner, nil when free
				SessionPlaceId = number?,
				LastUpdate = number,      -- heartbeat, refreshed on every write
				ForceLoad = string?,      -- another server asking for the lock
				Created = number,
				Saves = number,
			},
		}

	Lock handover:
		1. New server finds a live lock -> writes ForceLoad = its own session id.
		2. Owner sees ForceLoad on its next autosave -> saves, drops the lock,
		   fires Released("HandOver") so the owner can kick the stale client.
		3. New server claims on its next poll.
		4. If the owner never answers (crashed server), the new server steals the
		   lock after MaxForceLoadAttempts, or immediately if the heartbeat is
		   older than AssumeDeadSession.

	If ProfileStore/ProfileService is vendored into the place later, DataService
	is the only module that needs to change - this file is the swappable half.
]]

local DataStoreService = game:GetService("DataStoreService")
local HttpService = game:GetService("HttpService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local GameConfig = require(Shared.Config.GameConfig)
local Signal = require(Shared.Util.Signal)
local TableUtil = require(Shared.Util.TableUtil)

local CONFIG = GameConfig.Data

-- JobId is empty in Studio, so give each Studio run a unique owner id.
local SESSION_ID = if game.JobId ~= "" then game.JobId else "studio-" .. HttpService:GenerateGUID(false)

local SessionStore = {}

-- API access probe -----------------------------------------------------------
local apiAccess = nil

local function apiAccessEnabled()
	if not RunService:IsStudio() then
		return true
	end
	if apiAccess == nil then
		apiAccess = pcall(function()
			DataStoreService:GetDataStore("__SessionStoreProbe"):SetAsync("__probe", os.time())
		end)
		if not apiAccess then
			warn("[SessionStore] Studio API access is off - using an in-memory mock. DATA WILL NOT PERSIST.")
		end
	end
	return apiAccess
end

-- In-memory stand-in so the whole stack is testable in Studio without API access.
local MockStore = {}
MockStore.__index = MockStore

function MockStore.new()
	return setmetatable({ _data = {} }, MockStore)
end

function MockStore:UpdateAsync(key, transform)
	task.wait() -- keep the async contract
	local current = self._data[key]
	local updated = transform(if current then TableUtil.DeepCopy(current) else nil)
	if updated == nil then
		return current
	end
	self._data[key] = TableUtil.DeepCopy(updated)
	return TableUtil.DeepCopy(updated)
end

-- Request helpers ------------------------------------------------------------
local function waitForBudget(requestType)
	if not apiAccessEnabled() then
		return true
	end
	local deadline = os.clock() + CONFIG.BudgetTimeout
	while DataStoreService:GetRequestBudgetForRequestType(requestType) < 1 do
		if os.clock() > deadline then
			warn("[SessionStore] request budget exhausted, sending anyway")
			return false
		end
		task.wait(1)
	end
	return true
end

local function retryAsync(label, fn)
	local lastError
	for attempt = 1, CONFIG.MaxRetries do
		local ok, result = pcall(fn)
		if ok then
			return true, result
		end
		lastError = result
		warn(string.format("[SessionStore] %s failed (%d/%d): %s", label, attempt, CONFIG.MaxRetries, tostring(result)))
		if attempt < CONFIG.MaxRetries then
			task.wait(math.min(CONFIG.RetryBaseDelay * 2 ^ (attempt - 1), CONFIG.RetryMaxDelay))
		end
	end
	return false, lastError
end

-- Session --------------------------------------------------------------------
local Session = {}
Session.__index = Session

function Session.new(store, key, record, userIds)
	local self = setmetatable({}, Session)

	self.Key = key
	self.Data = record.Data
	self.Meta = record.Meta
	self.UserIds = userIds or {}
	self.Active = true
	self.Released = Signal.new() -- (reason: "Released" | "HandOver" | "LockLost")

	self._store = store
	self._writing = false

	task.spawn(function()
		self:_autosaveLoop()
	end)

	return self
end

function Session:_autosaveLoop()
	while self.Active do
		for _ = 1, CONFIG.AutosaveInterval do
			task.wait(1)
			if not self.Active then
				return
			end
		end
		self:SaveAsync(false)
	end
end

function Session:_write(release)
	local snapshot = TableUtil.DeepCopy(self.Data)

	if CONFIG.ValidateOnSave then
		local encoded, err = pcall(function()
			HttpService:JSONEncode(snapshot)
		end)
		if not encoded then
			warn(string.format("[SessionStore] %s holds unserializable data, refusing to save: %s", self.Key, tostring(err)))
			return false
		end
	end

	local outcome = "ok"

	local success = retryAsync("save:" .. self.Key, function()
		waitForBudget(Enum.DataStoreRequestType.UpdateAsync)
		self._store._ds:UpdateAsync(self.Key, function(old)
			outcome = "ok"

			local record = old
			if type(record) ~= "table" or type(record.Meta) ~= "table" then
				-- Key vanished (wiped mid-session). Recreate it under our lock.
				record = { Meta = { Created = os.time(), SessionJobId = SESSION_ID } }
			end

			local meta = record.Meta
			if meta.SessionJobId ~= SESSION_ID then
				outcome = "lockLost"
				return nil -- cancel the write; another server owns this save
			end
			if meta.ForceLoad and meta.ForceLoad ~= SESSION_ID then
				outcome = "handOver"
			end

			record.Data = snapshot
			meta.LastUpdate = os.time()
			meta.Saves = (meta.Saves or 0) + 1

			if release or outcome == "handOver" then
				meta.SessionJobId = nil
				meta.SessionPlaceId = nil
				meta.ForceLoad = nil
			end

			record.Meta = meta
			return record, self.UserIds
		end)
	end)

	if outcome == "lockLost" then
		self:_finish("LockLost")
		return false
	end
	if release then
		self:_finish("Released")
	elseif outcome == "handOver" then
		self:_finish("HandOver")
	end

	return success
end

--[[ Writes the current data. Serialized per session so an autosave and a
	 purchase-triggered save can never interleave. ]]
function Session:SaveAsync(release)
	if not self.Active and not release then
		return false
	end
	while self._writing do
		task.wait(0.1)
	end
	if not self.Active and not release then
		return false
	end

	self._writing = true
	local ok = self:_write(release == true)
	self._writing = false
	return ok
end

function Session:Release()
	if not self.Active then
		return false
	end
	return self:SaveAsync(true)
end

function Session:_finish(reason)
	if not self.Active then
		return
	end
	self.Active = false
	self.Released:Fire(reason)
end

-- Store ----------------------------------------------------------------------
local Store = {}
Store.__index = Store

function SessionStore.new(name, scope)
	local self = setmetatable({}, Store)
	self._ds = if apiAccessEnabled() then DataStoreService:GetDataStore(name, scope) else MockStore.new()
	return self
end

--[[ Claims the lock and returns a Session, or nil plus a reason string. ]]
function Store:LoadAsync(key, template, userIds)
	local forceSteal = false

	for attempt = 1, CONFIG.MaxForceLoadAttempts + 1 do
		local outcome, record

		local success = retryAsync("load:" .. key, function()
			waitForBudget(Enum.DataStoreRequestType.UpdateAsync)
			record = self._ds:UpdateAsync(key, function(old)
				outcome = nil

				local current = old
				if type(current) ~= "table" or type(current.Data) ~= "table" then
					current = {
						Data = TableUtil.DeepCopy(template),
						Meta = { Created = os.time(), IsNew = true },
					}
				end

				local meta = current.Meta or {}
				local now = os.time()
				local holder = meta.SessionJobId
				local dead = holder == nil or (now - (meta.LastUpdate or 0)) > CONFIG.AssumeDeadSession

				if dead or holder == SESSION_ID or forceSteal then
					meta.SessionJobId = SESSION_ID
					meta.SessionPlaceId = game.PlaceId
					meta.LastUpdate = now
					meta.ForceLoad = nil
					current.Meta = meta
					outcome = "claimed"
					return current, userIds
				end

				if meta.ForceLoad ~= SESSION_ID then
					meta.ForceLoad = SESSION_ID
					current.Meta = meta
					outcome = "requested"
					return current, userIds
				end

				outcome = "waiting"
				return nil -- nothing to write while we wait for the owner
			end)
		end)

		if not success then
			return nil, "DataStoreError"
		end
		if outcome == "claimed" then
			return Session.new(self, key, record, userIds)
		end

		if attempt == CONFIG.MaxForceLoadAttempts then
			warn(string.format("[SessionStore] %s did not release in time, stealing the lock", key))
			forceSteal = true
		end
		task.wait(CONFIG.ForceLoadInterval)
	end

	return nil, "SessionLocked"
end

SessionStore.SessionId = SESSION_ID

return SessionStore
