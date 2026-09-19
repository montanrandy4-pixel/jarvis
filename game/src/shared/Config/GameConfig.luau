--[[
	GameConfig
	Tuning knobs that are safe to replicate to the client. Anything secret or
	exploitable belongs in a server-only module instead.
]]

return {
	-- Persistence -----------------------------------------------------------
	Data = {
		Name = "PlayerData",
		Scope = "v1", -- bump to wipe everyone (test servers only)
		KeyFormat = "Player_%d",

		AutosaveInterval = 30, -- seconds between background writes
		AssumeDeadSession = 15 * 60, -- lock older than this is treated as crashed
		ForceLoadInterval = 6, -- seconds between steal attempts
		MaxForceLoadAttempts = 8, -- ~48s before we steal the lock outright

		MaxRetries = 5,
		RetryBaseDelay = 1,
		RetryMaxDelay = 16,

		BudgetTimeout = 30, -- max seconds to wait on DataStore request budget
		CloseTimeout = 25, -- BindToClose gives us 30s; leave headroom

		ValidateOnSave = true, -- JSON-encode check catches unserializable data
		MaxStoredReceipts = 60, -- purchase idempotency ring buffer
	},

	-- Economy ---------------------------------------------------------------
	Economy = {
		StartingCash = 0,
		MaxCash = 1e15, -- guards against float precision / exploit overflow
		LeaderstatName = "Cash",
		Abbreviations = { "", "K", "M", "B", "T", "Qa", "Qi" },
	},

	-- Session ---------------------------------------------------------------
	Session = {
		PlaytimeTickInterval = 60,
	},
}
