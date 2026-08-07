#!/bin/bash
set -f  # Disable globbing to prevent issues with * in patterns

# Read JSON input from stdin
input=$(cat)

# Verify jq is available — most of this script depends on it
if ! command -v jq >/dev/null 2>&1; then
    printf "statusline: jq not found\n"
    exit 0
fi

# Validate input JSON — fall back to empty object so all jq calls get valid input
if ! echo "$input" | jq -e . >/dev/null 2>&1; then
    input='{}'
fi

# Extract current directory from Claude Code context
cwd=$(echo "$input" | jq -r '.workspace.current_dir // .cwd // empty')

# Extract model display name and context usage percentage
model_name=$(echo "$input" | jq -r '.model.display_name // .model.id // empty')
ctx_pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0')

# Build a progress bar: [████░░░░░░] 42%
# Color: green <50%, yellow 50-75%, red >75%
build_progress_bar() {
    local pct="$1"
    local width=10
    local int_pct=${pct%.*}  # truncate to integer
    int_pct=$(( ${int_pct:-0} )) 2>/dev/null || int_pct=0
    local filled=$(( (int_pct * width + 50) / 100 ))
    [ "$filled" -gt "$width" ] && filled=$width
    [ "$filled" -lt 0 ] && filled=0
    local empty=$(( width - filled ))

    # Pick color based on percentage
    local color
    if [ "$int_pct" -lt 50 ]; then
        color="\033[32m"  # green
    elif [ "$int_pct" -lt 75 ]; then
        color="\033[33m"  # yellow
    else
        color="\033[31m"  # red
    fi

    local bar_filled=""
    local bar_empty=""
    for ((i=0; i<filled; i++)); do bar_filled+="█"; done
    for ((i=0; i<empty; i++)); do bar_empty+="░"; done

    printf "${color}%s\033[90m%s\033[0m" "$bar_filled" "$bar_empty"
}

# Determine username (GITHUB_USER or actual username)
if [ -n "${GITHUB_USER:-}" ]; then
    prompt_username="@${GITHUB_USER}"
else
    prompt_username="$(whoami 2>/dev/null || id -un 2>/dev/null || echo "user")"
fi

# Get shortened path (last 4 components, or show first/last 3 with ellipsis if > 5)
get_short_path() {
    local path="$1"
    # Replace home directory with ~
    path="${path/#$HOME/\~}"

    IFS='/' read -ra parts <<< "$path"
    local count=${#parts[@]}

    if [ $count -le 4 ]; then
        echo "$path"
    else
        # Show first part, ellipsis, and last 3 parts
        local first="${parts[0]}"
        local last3="${parts[-3]}/${parts[-2]}/${parts[-1]}"
        if [ -z "$first" ]; then
            echo "~/$last3"
        else
            echo "$first/…/$last3"
        fi
    fi
}

short_path=$(get_short_path "$cwd")

# Get git branch if in a git repo
git_info=""
if [ -n "$cwd" ]; then
    cd "$cwd" 2>/dev/null || true
fi

if timeout 3 git rev-parse --git-dir > /dev/null 2>&1; then
    # Check if git status should be hidden
    hide_status=$(git config --get devcontainers-theme.hide-status 2>/dev/null)
    hide_status_codespaces=$(git config --get codespaces-theme.hide-status 2>/dev/null)

    if [ "$hide_status" != "1" ] && [ "$hide_status_codespaces" != "1" ]; then
        branch=$(timeout 2 git --no-optional-locks symbolic-ref --short HEAD 2>/dev/null || timeout 2 git --no-optional-locks rev-parse --short HEAD 2>/dev/null)

        if [ ! -z "$branch" ]; then
            git_info="($branch"

            # Check if we should show dirty status
            show_dirty=$(git config --get devcontainers-theme.show-dirty 2>/dev/null)
            if [ "$show_dirty" = "1" ]; then
                if timeout 2 git --no-optional-locks ls-files --error-unmatch -m --directory --no-empty-directory -o --exclude-standard ":/*" > /dev/null 2>&1; then
                    git_info="$git_info ✗"
                fi
            fi

            git_info="$git_info)"
        fi
    fi
fi

# ===== ANSI color constants (used by both lines) =====
green="\033[32m"
yellow="\033[33m"
red="\033[31m"
orange="\033[38;5;214m"
dim="\033[90m"
reset="\033[0m"
bold_blue="\033[1;34m"
bold_cyan="\033[1;36m"
bold_magenta="\033[1;35m"

# ===== Usage limits: OAuth token from Linux credentials file =====
get_oauth_token() {
    local creds_file="${HOME}/.claude/.credentials.json"
    if [ -f "$creds_file" ]; then
        local token
        token=$(jq -r '.claudeAiOauth.accessToken // empty' "$creds_file" 2>/dev/null)
        if [ -n "$token" ] && [ "$token" != "null" ]; then
            echo "$token"
            return 0
        fi
    fi
    echo ""
}

# ===== Usage limits: non-blocking background cache refresh =====
# The script NEVER blocks on the network. It always reads from the cache file
# immediately (even if stale), and fires a background process to refresh it
# when the cache is older than cache_max_age seconds.
cache_file="/tmp/claude/statusline-usage-cache.json"
cache_lock="/tmp/claude/statusline-usage-cache.lock"
cache_max_age=60
mkdir -p /tmp/claude 2>/dev/null || { cache_file="/tmp/statusline-usage-cache.json"; cache_lock="/tmp/statusline-usage-cache.lock"; }

usage_data=""

# Always read whatever is cached first (non-blocking)
if [ -f "$cache_file" ]; then
    usage_data=$(cat "$cache_file" 2>/dev/null)
fi

# If the cached data is an error response, discard it so the display is blank
# rather than showing all-zero bars.  Also delete the cache file so the
# freshness check below immediately sets needs_refresh=true regardless of mtime.
if [ -n "$usage_data" ]; then
    cached_error_type=$(echo "$usage_data" | jq -r '.type // empty' 2>/dev/null)
    if [ "$cached_error_type" = "error" ]; then
        usage_data=""
        rm -f "$cache_file" 2>/dev/null || true
    fi
fi

# Check if cache needs refreshing — but only launch a background refresh,
# never block the status line script waiting for the network.
needs_refresh=true
if [ -f "$cache_file" ]; then
    cache_mtime=$(stat -c %Y "$cache_file" 2>/dev/null)
    now=$(date +%s 2>/dev/null)
    # Guard: if either value is empty (command failure), keep needs_refresh=true
    if [ -n "${cache_mtime:-}" ] && [ -n "${now:-}" ]; then
        cache_age=$(( now - cache_mtime ))
        if [ "$cache_age" -lt "$cache_max_age" ]; then
            needs_refresh=false
        fi
    fi
fi

if $needs_refresh; then
    # Fire off a background refresh so this invocation is not blocked.
    # Use a lock file (via flock if available) to avoid concurrent curls.
    (
        # Acquire lock if flock is available; otherwise proceed without locking
        if command -v flock >/dev/null 2>&1; then
            exec 9>"$cache_lock"
            flock -n 9 || exit 0
        fi

        token=$(get_oauth_token)
        if [ -n "${token:-}" ] && [ "$token" != "null" ] && command -v curl >/dev/null 2>&1; then
            response=$(curl -s --max-time 8 \
                -H "Accept: application/json" \
                -H "Content-Type: application/json" \
                -H "Authorization: Bearer $token" \
                -H "anthropic-beta: oauth-2025-04-20" \
                -H "User-Agent: claude-code/2.1.34" \
                "https://api.anthropic.com/api/oauth/usage" 2>/dev/null)
            # Atomic write: write to temp file then rename to avoid partial reads.
            # Only cache successful responses — never cache error objects, otherwise
            # the status line will display all-zero bars until the next successful fetch.
            if [ -n "${response:-}" ] && echo "$response" | jq . >/dev/null 2>&1; then
                response_type=$(echo "$response" | jq -r '.type // empty' 2>/dev/null)
                if [ "$response_type" != "error" ]; then
                    tmp_cache="${cache_file}.tmp.$$"
                    printf '%s' "$response" > "$tmp_cache" && mv -f "$tmp_cache" "$cache_file" 2>/dev/null || rm -f "$tmp_cache" 2>/dev/null
                fi
            fi
        fi
    ) &
    disown $! 2>/dev/null || true
fi

# Build a colored ●/○ progress bar for usage limits
build_bar() {
    local pct=$1
    local width=$2
    # Coerce to integer — arithmetic with empty or non-numeric string causes errors
    pct=$(( ${pct:-0} )) 2>/dev/null || pct=0
    [ "$pct" -lt 0 ] && pct=0
    [ "$pct" -gt 100 ] && pct=100

    local filled=$(( pct * width / 100 ))
    local empty=$(( width - filled ))

    local bar_color
    if [ "$pct" -ge 90 ]; then bar_color="$red"
    elif [ "$pct" -ge 70 ]; then bar_color="$yellow"
    elif [ "$pct" -ge 50 ]; then bar_color="$orange"
    else bar_color="$green"
    fi

    local filled_str="" empty_str=""
    local i
    for ((i=0; i<filled; i++)); do filled_str+="●"; done
    for ((i=0; i<empty; i++)); do empty_str+="○"; done

    printf "${bar_color}${filled_str}${dim}${empty_str}${reset}"
}

# Format ISO-8601 reset time to compact local time using GNU date (Linux).
#
# The Anthropic API returns UTC timestamps (e.g. "2025-06-15T17:30:00Z" or
# "2025-06-15T17:30:00+00:00").  GNU date -d handles both forms: the timezone
# designator is parsed, the value is converted to a UTC epoch, and then the
# second date call formats that epoch in the user's local timezone (no TZ=
# override, so the system timezone applies).
#
# Defensive: if the API ever returns a bare datetime with no timezone designator,
# we append "Z" so date -d always treats it as UTC rather than local time.
format_reset_time() {
    local iso_str="$1"
    local style="$2"
    [ -z "$iso_str" ] || [ "$iso_str" = "null" ] && return

    # Ensure a UTC designator is present so GNU date never guesses local time.
    # If the string already ends with Z or contains + or - after the time part,
    # leave it alone; otherwise append Z.
    local safe_iso="$iso_str"
    case "$iso_str" in
        *Z|*+*:*|*-*:*T*) ;;   # already has a timezone designator
        *) safe_iso="${iso_str}Z" ;;
    esac

    # Step 1: parse the ISO string (with timezone) to a UTC epoch.
    local epoch
    epoch=$(date -d "${safe_iso}" +%s 2>/dev/null)
    [ -z "$epoch" ] && return

    # Step 2: format the epoch in the user's local timezone.
    case "$style" in
        time)
            date -d "@$epoch" +"%l:%M%P" 2>/dev/null | sed 's/^ //'
            ;;
        datetime)
            date -d "@$epoch" +"%b %-d, %l:%M%P" 2>/dev/null | sed 's/  / /g; s/^ //'
            ;;
        *)
            date -d "@$epoch" +"%b %-d" 2>/dev/null
            ;;
    esac
}

# === Build output line 1 ===
# Left side: username ➜ path (branch)
printf "${green}%s${reset} ➜ ${bold_blue}%s${reset} " "$prompt_username" "$short_path"

if [ -n "$git_info" ]; then
    printf "${bold_cyan}%s${reset} " "$git_info"
fi

# Right side: model | progress bar pct%
if [ -n "$model_name" ] || [ -n "$ctx_pct" ]; then
    printf "${dim}│${reset} "

    if [ -n "$model_name" ]; then
        printf "${bold_magenta}%s${reset} " "$model_name"
    fi

    if [ -n "$ctx_pct" ] && [ "$ctx_pct" != "0" ] && [ "$ctx_pct" != "null" ]; then
        int_pct=${ctx_pct%.*}
        build_progress_bar "$int_pct"
        printf " ${dim}%s%%${reset}" "$int_pct"
    fi
fi

# === Build output line 2: session / weekly / extra usage limits ===
line2=""
sep=" | "

if [ -n "$usage_data" ] && echo "$usage_data" | jq -e . >/dev/null 2>&1; then
    bar_width=10

    # ---- 5-hour (session) ----
    # utilization is already a percentage (0–100) from the API
    five_hour_pct=$(echo "$usage_data" | jq -r '.five_hour.utilization // 0' | awk '{printf "%.0f", $1}')
    five_hour_reset_iso=$(echo "$usage_data" | jq -r '.five_hour.resets_at // empty')
    five_hour_reset=$(format_reset_time "$five_hour_reset_iso" "time")
    five_hour_bar=$(build_bar "$five_hour_pct" "$bar_width")

    # ---- 7-day (weekly) ----
    # utilization is already a percentage (0–100) from the API
    seven_day_pct=$(echo "$usage_data" | jq -r '.seven_day.utilization // 0' | awk '{printf "%.0f", $1}')
    seven_day_reset_iso=$(echo "$usage_data" | jq -r '.seven_day.resets_at // empty')
    seven_day_reset=$(format_reset_time "$seven_day_reset_iso" "datetime")
    seven_day_bar=$(build_bar "$seven_day_pct" "$bar_width")

    line2="session: ${five_hour_bar} ${five_hour_pct}%"
    [ -n "$five_hour_reset" ] && line2+=" (resets ${five_hour_reset})"
    line2+="${sep}"
    line2+="weekly: ${seven_day_bar} ${seven_day_pct}%"
    [ -n "$seven_day_reset" ] && line2+=" (resets ${seven_day_reset})"

    # ---- Extra usage (only if enabled) ----
    extra_enabled=$(echo "$usage_data" | jq -r '.extra_usage.is_enabled // false')
    if [ "$extra_enabled" = "true" ]; then
        # utilization is already a percentage (0–100) from the API
        extra_pct=$(echo "$usage_data" | jq -r '.extra_usage.utilization // 0' | awk '{printf "%.0f", $1}')
        extra_used=$(echo "$usage_data" | jq -r '.extra_usage.used_credits // 0' | awk '{printf "%.2f", $1/100}')
        extra_limit=$(echo "$usage_data" | jq -r '.extra_usage.monthly_limit // 0' | awk '{printf "%.2f", $1/100}')
        extra_bar=$(build_bar "$extra_pct" "$bar_width")
        line2+="${sep}extra: ${extra_bar} \$${extra_used}/\$${extra_limit}"
    fi
fi

if [ -n "$line2" ]; then
    printf '\n'
    printf '%s' "$line2"
fi

exit 0
