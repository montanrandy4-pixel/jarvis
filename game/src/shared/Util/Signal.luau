--[[
	Signal
	Lightweight Roblox-style event object. Used instead of BindableEvents so we
	can pass tables/functions between systems without serialization cost.
]]

local Connection = {}
Connection.__index = Connection

function Connection.new(signal, handler)
	return setmetatable({
		Connected = true,
		_signal = signal,
		_handler = handler,
	}, Connection)
end

function Connection:Disconnect()
	if not self.Connected then
		return
	end
	self.Connected = false

	local handlers = self._signal._handlers
	local index = table.find(handlers, self)
	if index then
		table.remove(handlers, index)
	end
end

local Signal = {}
Signal.__index = Signal

function Signal.new()
	return setmetatable({ _handlers = {} }, Signal)
end

function Signal:Connect(handler)
	assert(type(handler) == "function", "Signal:Connect expects a function")
	local connection = Connection.new(self, handler)
	table.insert(self._handlers, connection)
	return connection
end

--[[ Fires every handler on its own thread so one erroring listener cannot
	 stall the caller (important for data / purchase pipelines). ]]
function Signal:Fire(...)
	for _, connection in table.clone(self._handlers) do
		if connection.Connected then
			task.spawn(connection._handler, ...)
		end
	end
end

function Signal:Wait()
	local thread = coroutine.running()
	local connection
	connection = self:Connect(function(...)
		connection:Disconnect()
		task.spawn(thread, ...)
	end)
	return coroutine.yield()
end

function Signal:Destroy()
	for _, connection in table.clone(self._handlers) do
		connection:Disconnect()
	end
	table.clear(self._handlers)
end

return Signal
