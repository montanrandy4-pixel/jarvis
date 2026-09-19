--[[
	Net
	Single source of truth for remotes. The server creates them on demand, the
	client waits for them. Names are namespaced strings ("Data/Changed") so a
	system never has to know where in the tree a remote lives.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local FOLDER_NAME = "Remotes"
local WAIT_TIMEOUT = 30

local Net = {}
local cache = {}

local function getFolder()
	if RunService:IsServer() then
		local folder = ReplicatedStorage:FindFirstChild(FOLDER_NAME)
		if not folder then
			folder = Instance.new("Folder")
			folder.Name = FOLDER_NAME
			folder.Parent = ReplicatedStorage
		end
		return folder
	end

	local folder = ReplicatedStorage:WaitForChild(FOLDER_NAME, WAIT_TIMEOUT)
	assert(folder, "[Net] Remotes folder never replicated")
	return folder
end

local function resolve(className, name)
	local cached = cache[name]
	if cached then
		return cached
	end

	local folder = getFolder()
	local remote

	if RunService:IsServer() then
		remote = folder:FindFirstChild(name)
		if not remote then
			remote = Instance.new(className)
			remote.Name = name
			remote.Parent = folder
		end
	else
		remote = folder:WaitForChild(name, WAIT_TIMEOUT)
		assert(remote, string.format("[Net] remote %q never replicated", name))
	end

	cache[name] = remote
	return remote
end

function Net.Event(name)
	return resolve("RemoteEvent", name)
end

function Net.Function(name)
	return resolve("RemoteFunction", name)
end

--[[ Cheap per-player token bucket. Every server-side remote handler that a
	 client can spam should sit behind one of these. ]]
function Net.RateLimiter(maxRequests, windowSeconds)
	local buckets = {}

	return function(player)
		local now = os.clock()
		local bucket = buckets[player]
		if not bucket or now - bucket.start > windowSeconds then
			buckets[player] = { start = now, count = 1 }
			return true
		end
		if bucket.count >= maxRequests then
			return false
		end
		bucket.count += 1
		return true
	end
end

return Net
