--[[
	DataService
	Owns every player's save file. Nothing else in the game touches a DataStore.

	Contract for other services:
		DataService:Get(player)                  -> data table or nil
		DataService:WaitFor(player, timeout)     -> data table or nil
		DataService:Set(player, path, value)     -> boolean
		DataService:Increment(player, path, n)   -> new value or nil
		DataService:SaveAsync(player)            -> boolean (use after purchases)
		DataService.ProfileLoaded : (player, data)
		DataService.ProfileReleased : (player)
		DataService.Changed : (player, path, value)

	Paths are dotted strings: "Cash", "Tycoon.Rebirths", "Settings.MusicEnabled".
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerScriptService = game:GetService("ServerScriptService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local DataTemplate = require(Shared.Config.DataTemplate)
local GameConfig = require(Shared.Config.GameConfig)
local Net = require(Shared.Util.Net)
local Signal = require(Shared.Util.Signal)
local TableUtil = require(Shared.Util.TableUtil)

local SessionStore = require(ServerScriptService.Server.Modules.SessionStore)

local CONFIG = GameConfig.Data
local KICK_LOCKED = "We couldn't load your save (it may still be open on another server). Please rejoin in a moment."
local KICK_STOLEN = "Your save was opened in another server. Please rejoin."

local DataService = {
	Name = "DataService",
	Priority = 100, -- starts before anything that reads player data
}

function DataService:Init()
	self._sessions = {}
	self._loading = {}
	self._store = SessionStore.new(CONFIG.Name, CONFIG.Scope)

	self.ProfileLoaded = Signal.new()
	self.ProfileReleased = Signal.new()
	self.Changed = Signal.new()

	self._changedRemote = Net.Event("Data/Changed")
	self._getRemote = Net.Function("Data/Get")
end

function DataService:Start()
	self._getRemote.OnServerInvoke = function(player)
		return self:_snapshot(player)
	end

	Players.PlayerAdded:Connect(function(player)
		self:_onPlayerAdded(player)
	end)
	Players.PlayerRemoving:Connect(function(player)
		self:_onPlayerRemoving(player)
	end)

	-- Players who joined while the framework was booting.
	for _, player in Players:GetPlayers() do
		task.spawn(function()
			self:_onPlayerAdded(player)
		end)
	end

	game:BindToClose(function()
		self:_onClose()
	end)

	task.spawn(function()
		self:_playtimeLoop()
	end)
end

-- Loading --------------------------------------------------------------------
function DataService:_onPlayerAdded(player)
	if self._sessions[player] or self._loading[player] then
		return
	end
	self._loading[player] = true

	local key = string.format(CONFIG.KeyFormat, player.UserId)
	local session, err = self._store:LoadAsync(key, DataTemplate.Data, { player.UserId })

	self._loading[player] = nil

	if not player:IsDescendantOf(Players) then
		-- Left mid-load: drop the lock so their next server can claim it.
		if session then
			task.spawn(function()
				session:Release()
			end)
		end
		return
	end

	if not session then
		warn(string.format("[DataService] load failed for %s: %s", player.Name, tostring(err)))
		player:Kick(KICK_LOCKED)
		return
	end

	local data = session.Data
	DataTemplate.Migrate(data)
	TableUtil.Reconcile(data, DataTemplate.Data)

	local now = os.time()
	if data.Stats.FirstJoin == 0 then
		data.Stats.FirstJoin = now
	end
	data.Stats.LastJoin = now
	data.Stats.Joins += 1
	self:_pruneExpiredBoosts(data)

	self._sessions[player] = session

	session.Released:Connect(function(reason)
		self._sessions[player] = nil
		self.ProfileReleased:Fire(player)
		if reason ~= "Released" and player:IsDescendantOf(Players) then
			-- Another server took ownership; this client's state is now stale.
			player:Kick(KICK_STOLEN)
		end
	end)

	self:_setupLeaderstats(player, data)
	self:_pushSnapshot(player)
	self.ProfileLoaded:Fire(player, data)
end

function DataService:_onPlayerRemoving(player)
	local session = self._sessions[player]
	if not session then
		return
	end
	self._sessions[player] = nil
	task.spawn(function()
		session:Release()
	end)
end

function DataService:_onClose()
	local pending = 0
	for player, session in self._sessions do
		pending += 1
		self._sessions[player] = nil
		task.spawn(function()
			session:Release()
			pending -= 1
		end)
	end

	local deadline = os.clock() + CONFIG.CloseTimeout
	while pending > 0 and os.clock() < deadline do
		task.wait(0.1)
	end
	if pending > 0 then
		warn(string.format("[DataService] shutdown timed out with %d unsaved session(s)", pending))
	end
end

-- Reads / writes -------------------------------------------------------------
function DataService:GetSession(player)
	return self._sessions[player]
end

function DataService:Get(player)
	local session = self._sessions[player]
	return session and session.Data or nil
end

function DataService:WaitFor(player, timeout)
	local deadline = os.clock() + (timeout or 20)
	while os.clock() < deadline do
		local data = self:Get(player)
		if data then
			return data
		end
		if not player:IsDescendantOf(Players) then
			return nil
		end
		task.wait(0.1)
	end
	return nil
end

function DataService:Set(player, path, value)
	local data = self:Get(player)
	if not data then
		return false
	end
	local ok = TableUtil.Set(data, path, value)
	if not ok then
		warn(string.format("[DataService] bad path %q for %s", tostring(path), player.Name))
		return false
	end
	self:_onChanged(player, path, value)
	return true
end

function DataService:Increment(player, path, amount)
	local data = self:Get(player)
	if not data then
		return nil
	end
	local current = TableUtil.Get(data, path)
	if type(current) ~= "number" then
		warn(string.format("[DataService] cannot increment non-number %q", tostring(path)))
		return nil
	end
	local updated = current + amount
	TableUtil.Set(data, path, updated)
	self:_onChanged(player, path, updated)
	return updated
end

--[[ Force an immediate write. Call after granting anything a player paid for,
	 before telling Roblox the receipt was handled. ]]
function DataService:SaveAsync(player)
	local session = self._sessions[player]
	if not session then
		return false
	end
	return session:SaveAsync(false)
end

-- Replication ----------------------------------------------------------------
function DataService:_snapshot(player)
	local data = self:WaitFor(player, 20)
	if not data then
		return nil
	end
	local snapshot = {}
	for key in DataTemplate.Replicated do
		local value = data[key]
		snapshot[key] = if type(value) == "table" then TableUtil.DeepCopy(value) else value
	end
	return snapshot
end

function DataService:_pushSnapshot(player)
	local snapshot = self:_snapshot(player)
	if snapshot then
		self._changedRemote:FireClient(player, nil, snapshot)
	end
end

function DataService:_onChanged(player, path, value)
	local root = string.split(path, ".")[1]

	if DataTemplate.Replicated[root] then
		self._changedRemote:FireClient(player, path, value)
	end
	if root == GameConfig.Economy.LeaderstatName then
		self:_updateLeaderstat(player, value)
	end
	self.Changed:Fire(player, path, value)
end

function DataService:_setupLeaderstats(player, data)
	local leaderstats = Instance.new("Folder")
	leaderstats.Name = "leaderstats"

	local cash = Instance.new("IntValue")
	cash.Name = GameConfig.Economy.LeaderstatName
	cash.Value = math.floor(data.Cash)
	cash.Parent = leaderstats

	local rebirths = Instance.new("IntValue")
	rebirths.Name = "Rebirths"
	rebirths.Value = data.Tycoon.Rebirths
	rebirths.Parent = leaderstats

	leaderstats.Parent = player
end

function DataService:_updateLeaderstat(player, value)
	local leaderstats = player:FindFirstChild("leaderstats")
	local stat = leaderstats and leaderstats:FindFirstChild(GameConfig.Economy.LeaderstatName)
	if stat then
		stat.Value = math.floor(math.clamp(value, 0, 2 ^ 31 - 1))
	end
end

-- Housekeeping ---------------------------------------------------------------
function DataService:_pruneExpiredBoosts(data)
	local now = os.time()
	for key, expiry in data.Boosts do
		if type(expiry) ~= "number" or expiry <= now then
			data.Boosts[key] = nil
		end
	end
end

function DataService:_playtimeLoop()
	local interval = GameConfig.Session.PlaytimeTickInterval
	while true do
		task.wait(interval)
		for player, session in self._sessions do
			if session.Active then
				session.Data.Stats.Playtime += interval
				self.Changed:Fire(player, "Stats.Playtime", session.Data.Stats.Playtime)
			end
		end
	end
end

return DataService
