--[[
	DataController
	Read-only mirror of the replicated half of the player's save. UI reads from
	here instead of from remotes, so every screen shares one source of truth.

		DataController:Get("Cash")
		DataController:Observe("Cash", function(value) label.Text = value end)
		DataController.Loaded:Wait()

	This is a convenience cache, never authority: the server re-checks everything.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Net = require(Shared.Util.Net)
local Signal = require(Shared.Util.Signal)
local TableUtil = require(Shared.Util.TableUtil)

local FETCH_RETRY = 3

local DataController = {
	Name = "DataController",
	Priority = 100,
}

function DataController:Init()
	self._data = {}
	self._queued = {} -- deltas that land before the snapshot does
	self._observers = {} -- [path] = { fn, ... }

	self.IsLoaded = false
	self.Loaded = Signal.new()
	self.Changed = Signal.new() -- (path, value)
end

function DataController:Start()
	-- Connect first so nothing is lost while the snapshot request is in flight.
	Net.Event("Data/Changed").OnClientEvent:Connect(function(path, value)
		if path == nil then
			self:_applySnapshot(value)
		elseif self.IsLoaded then
			self:_applyChange(path, value)
		else
			table.insert(self._queued, { path = path, value = value })
		end
	end)

	task.spawn(function()
		local remote = Net.Function("Data/Get")
		for _ = 1, FETCH_RETRY do
			if self.IsLoaded then
				return
			end
			local ok, snapshot = pcall(function()
				return remote:InvokeServer()
			end)
			if ok and snapshot then
				self:_applySnapshot(snapshot)
				return
			end
			task.wait(2)
		end
		warn("[DataController] could not fetch player data")
	end)
end

function DataController:_applySnapshot(snapshot)
	if self.IsLoaded or type(snapshot) ~= "table" then
		return
	end

	self._data = snapshot
	self.IsLoaded = true

	for _, queued in self._queued do
		TableUtil.Set(self._data, queued.path, queued.value)
	end
	table.clear(self._queued)

	self.Loaded:Fire(self._data)
	for path in self._observers do
		self:_notify(path, self:Get(path))
	end
end

function DataController:_applyChange(path, value)
	TableUtil.Set(self._data, path, value)
	self.Changed:Fire(path, value)

	-- Notify the exact path and every ancestor ("Tycoon" hears "Tycoon.Rebirths").
	local keys = string.split(path, ".")
	local prefix = nil
	for _, key in keys do
		prefix = if prefix then prefix .. "." .. key else key
		self:_notify(prefix, self:Get(prefix))
	end
end

function DataController:_notify(path, value)
	local observers = self._observers[path]
	if not observers then
		return
	end
	for _, callback in table.clone(observers) do
		task.spawn(callback, value)
	end
end

function DataController:Get(path)
	if path == nil then
		return self._data
	end
	return TableUtil.Get(self._data, path)
end

--[[ Calls back immediately with the current value (once loaded) and on every
	 change after. Returns a disconnect function. ]]
function DataController:Observe(path, callback)
	local observers = self._observers[path]
	if not observers then
		observers = {}
		self._observers[path] = observers
	end
	table.insert(observers, callback)

	if self.IsLoaded then
		task.spawn(callback, self:Get(path))
	end

	return function()
		local index = table.find(observers, callback)
		if index then
			table.remove(observers, index)
		end
	end
end

return DataController
