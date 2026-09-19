--[[
	Server bootstrapper
	Requires every ModuleScript in Services, injects a shared registry, then runs
	Init on all of them (highest Priority first) before running Start on any.

	Service contract:
		return {
			Name = "MyService",   -- optional, defaults to the ModuleScript name
			Priority = 50,        -- optional, higher runs earlier
			Init = function(self) end,   -- create state and signals, no cross-calls
			Start = function(self) end,  -- wire things up, other services are ready
		}

	Inside a service, `self.Services.OtherService` is the registry - never require
	another service directly, that is how circular requires start.
]]

local START_TIME = os.clock()

local services = {}
local ordered = {}

for _, module in script.Services:GetChildren() do
	if not module:IsA("ModuleScript") then
		continue
	end

	local ok, service = pcall(require, module)
	if not ok then
		warn(string.format("[Bootstrap] failed to require %s: %s", module.Name, tostring(service)))
		continue
	end
	if type(service) ~= "table" then
		warn(string.format("[Bootstrap] %s did not return a table", module.Name))
		continue
	end

	service.Name = service.Name or module.Name
	service.Priority = service.Priority or 0
	service.Services = services

	services[service.Name] = service
	table.insert(ordered, service)
end

table.sort(ordered, function(a, b)
	if a.Priority == b.Priority then
		return a.Name < b.Name
	end
	return a.Priority > b.Priority
end)

for _, service in ordered do
	if type(service.Init) == "function" then
		local ok, err = pcall(service.Init, service)
		if not ok then
			warn(string.format("[Bootstrap] %s:Init errored: %s", service.Name, tostring(err)))
		end
	end
end

for _, service in ordered do
	if type(service.Start) == "function" then
		task.spawn(function()
			local ok, err = pcall(service.Start, service)
			if not ok then
				warn(string.format("[Bootstrap] %s:Start errored: %s", service.Name, tostring(err)))
			end
		end)
	end
end

print(string.format("[Bootstrap] %d services started in %.0fms", #ordered, (os.clock() - START_TIME) * 1000))
