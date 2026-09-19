--[[
	Client bootstrapper
	Mirror of the server loader. Controllers get `self.Controllers` for lookups.
]]

local START_TIME = os.clock()

local controllers = {}
local ordered = {}

for _, module in script.Controllers:GetChildren() do
	if not module:IsA("ModuleScript") then
		continue
	end

	local ok, controller = pcall(require, module)
	if not ok then
		warn(string.format("[Bootstrap] failed to require %s: %s", module.Name, tostring(controller)))
		continue
	end
	if type(controller) ~= "table" then
		warn(string.format("[Bootstrap] %s did not return a table", module.Name))
		continue
	end

	controller.Name = controller.Name or module.Name
	controller.Priority = controller.Priority or 0
	controller.Controllers = controllers

	controllers[controller.Name] = controller
	table.insert(ordered, controller)
end

table.sort(ordered, function(a, b)
	if a.Priority == b.Priority then
		return a.Name < b.Name
	end
	return a.Priority > b.Priority
end)

for _, controller in ordered do
	if type(controller.Init) == "function" then
		local ok, err = pcall(controller.Init, controller)
		if not ok then
			warn(string.format("[Bootstrap] %s:Init errored: %s", controller.Name, tostring(err)))
		end
	end
end

for _, controller in ordered do
	if type(controller.Start) == "function" then
		task.spawn(function()
			local ok, err = pcall(controller.Start, controller)
			if not ok then
				warn(string.format("[Bootstrap] %s:Start errored: %s", controller.Name, tostring(err)))
			end
		end)
	end
end

print(string.format("[Bootstrap] %d controllers started in %.0fms", #ordered, (os.clock() - START_TIME) * 1000))
