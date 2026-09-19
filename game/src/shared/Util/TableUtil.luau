--[[
	TableUtil
	Deep copy / reconcile / dotted-path access. The data layer leans on these
	for template reconciliation and for replicating granular changes.
]]

local TableUtil = {}

function TableUtil.DeepCopy(source)
	local copy = table.clone(source)
	for key, value in copy do
		if type(value) == "table" then
			copy[key] = TableUtil.DeepCopy(value)
		end
	end
	return copy
end

--[[ Fills in keys the template has but the stored data does not. This is what
	 lets us add new stats in a later update without wiping anybody. ]]
function TableUtil.Reconcile(target, template)
	for key, value in template do
		if target[key] == nil then
			if type(value) == "table" then
				target[key] = TableUtil.DeepCopy(value)
			else
				target[key] = value
			end
		elseif type(value) == "table" and type(target[key]) == "table" then
			TableUtil.Reconcile(target[key], value)
		end
	end
	return target
end

local function splitPath(path)
	if type(path) == "table" then
		return path
	end
	return string.split(path, ".")
end

function TableUtil.Get(root, path)
	local keys = splitPath(path)
	local node = root
	for _, key in keys do
		if type(node) ~= "table" then
			return nil
		end
		node = node[key]
	end
	return node
end

--[[ Returns success, previousValue. Refuses to create intermediate tables so a
	 typo in a path cannot silently grow the save file. ]]
function TableUtil.Set(root, path, value)
	local keys = splitPath(path)
	local node = root
	for index = 1, #keys - 1 do
		node = node[keys[index]]
		if type(node) ~= "table" then
			return false, nil
		end
	end
	local finalKey = keys[#keys]
	local previous = node[finalKey]
	node[finalKey] = value
	return true, previous
end

function TableUtil.Count(source)
	local count = 0
	for _ in source do
		count += 1
	end
	return count
end

return TableUtil
