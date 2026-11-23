"""
AntigravityFlow - Quota & Allowance Service
============================================
Fetches live quota consumption from Antigravity Language Server:
POST /exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary
POST /exa.language_server_pb.LanguageServerService/GetUserStatus

Computes approximate remaining word allowances, hours of speech remaining,
weekly and 5-hour rolling limits, with plan-aware projections (Free, Pro, Ultra).
"""

import http.client
import json
import ssl
import time
from datetime import datetime
import llm_refiner
import db

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Word allowances approximations based on model token weights and tier capacities
PLAN_SPECS = {
    "free": {
        "name": "Free",
        "badge": "Free Tier",
        "weekly_words": 15000,
        "five_hour_words": 2500,
        "description": "Starter allowance for casual dictation."
    },
    "pro": {
        "name": "Pro",
        "badge": "Pro Plan (Active)",
        "weekly_words": 150000,
        "five_hour_words": 25000,
        "description": "High-capacity allowance for power users and developers."
    },
    "ultra": {
        "name": "Ultra",
        "badge": "Ultra Unlimited",
        "weekly_words": 600000,
        "five_hour_words": 80000,
        "description": "Maximum capacity for enterprise and heavy generative tasks."
    }
}

def format_relative_time(iso_time_str: str) -> str:
    """Converts ISO resetTime into a clean relative countdown string."""
    if not iso_time_str:
        return "Soon"
    try:
        clean_iso = iso_time_str.replace("Z", "+00:00")
        target_dt = datetime.fromisoformat(clean_iso)
        now_dt = datetime.now(target_dt.tzinfo)
        diff = target_dt - now_dt
        total_seconds = int(diff.total_seconds())
        if total_seconds <= 0:
            return "Refreshing now"
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes}m")
        return "in " + " ".join(parts) if parts else "in < 1m"
    except Exception:
        return iso_time_str

def get_quota_summary() -> dict:
    """Queries live Antigravity Language Server and returns structured quota and word remaining metrics."""
    try:
        port, csrf_token = llm_refiner.discover_server()
    except Exception as e:
        cached = db.get_user_profile()
        if cached:
            return {
                "success": True,
                "cached": True,
                "user": {
                    "name": cached.get("user_name", "") or cached.get("name", ""),
                    "email": cached.get("user_email", "") or cached.get("email", ""),
                    "planName": cached.get("plan_name", "") or cached.get("planName", ""),
                    "planBadge": cached.get("plan_badge", "") or cached.get("planBadge", "")
                },
                "gemini": {
                    "weeklyPercentRemaining": float(cached.get("weekly_percent", 100.0)),
                    "fiveHourPercentRemaining": float(cached.get("five_hour_percent", 100.0)),
                    "weeklyResetHuman": cached.get("weekly_reset", "Active"),
                    "fiveHourResetHuman": cached.get("five_hour_reset", "Active")
                }
            }
        return {"success": False, "error": f"Language server offline: {e}"}

    headers = {
        "content-type": "application/grpc-web+json",
        "x-codeium-csrf-token": csrf_token,
        "x-grpc-web": "1"
    }

    # 1. Retrieve User Quota Summary
    quota_raw = None
    try:
        conn = http.client.HTTPSConnection('127.0.0.1', port, context=ctx, timeout=3.0)
        frame = llm_refiner.make_grpc_web_frame(b'{}')
        conn.request("POST", "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary", body=frame, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        if res.status == 200 and len(raw) >= 5:
            l = int.from_bytes(raw[1:5], "big")
            quota_raw = json.loads(raw[5:5+l].decode("utf-8")).get("response", {})
    except Exception as e:
        print(f"[QuotaService] RetrieveUserQuotaSummary error: {e}")

    # 2. Retrieve User Status & Plan
    user_status = {}
    plan_name = "Pro"
    user_name = "User"
    user_email = ""
    try:
        conn = http.client.HTTPSConnection('127.0.0.1', port, context=ctx, timeout=3.0)
        frame = llm_refiner.make_grpc_web_frame(b'{}')
        conn.request("POST", "/exa.language_server_pb.LanguageServerService/GetUserStatus", body=frame, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        if res.status == 200 and len(raw) >= 5:
            l = int.from_bytes(raw[1:5], "big")
            status_data = json.loads(raw[5:5+l].decode("utf-8")).get("userStatus", {})
            user_status = status_data
            user_name = status_data.get("name") or user_name
            user_email = status_data.get("email") or ""
            plan_info = status_data.get("planStatus", {}).get("planInfo", {})
            raw_plan = plan_info.get("planName") or "Pro"
            plan_name = raw_plan.capitalize()
    except Exception as e:
        print(f"[QuotaService] GetUserStatus error: {e}")

    detected_plan_key = "pro"
    lower_plan = plan_name.lower()
    if "ultra" in lower_plan:
        detected_plan_key = "ultra"
        plan_badge = "Google AI Ultra"
    elif "free" in lower_plan or "basic" in lower_plan or "starter" in lower_plan:
        detected_plan_key = "free"
        plan_badge = "Google AI Free"
    else:
        detected_plan_key = "pro"
        plan_badge = f"Google AI {plan_name}" if "Google" not in plan_name else plan_name

    # Parse Quota Groups
    parsed_groups = {}
    gemini_summary = {
        "weekly_remaining_fraction": 1.0,
        "weekly_reset_time": "",
        "weekly_reset_human": "",
        "weekly_description": "",
        "five_hour_remaining_fraction": 1.0,
        "five_hour_reset_time": "",
        "five_hour_reset_human": "",
        "five_hour_description": ""
    }
    third_party_summary = {
        "weekly_remaining_fraction": 1.0,
        "weekly_reset_time": "",
        "weekly_reset_human": "",
        "weekly_description": "",
        "five_hour_remaining_fraction": 1.0,
        "five_hour_reset_time": "",
        "five_hour_reset_human": "",
        "five_hour_description": ""
    }

    if quota_raw and "groups" in quota_raw:
        for g in quota_raw["groups"]:
            d_name = g.get("displayName", "")
            buckets = g.get("buckets", [])
            b_map = {}
            for b in buckets:
                win = b.get("window", "")
                frac = float(b.get("remainingFraction", 1.0))
                reset_t = b.get("resetTime", "")
                desc = b.get("description", "")
                b_map[win] = {
                    "bucketId": b.get("bucketId"),
                    "window": win,
                    "displayName": b.get("displayName"),
                    "remainingFraction": round(frac, 4),
                    "remainingPercent": round(frac * 100.0, 1),
                    "usedPercent": round((1.0 - frac) * 100.0, 1),
                    "resetTime": reset_t,
                    "resetHuman": format_relative_time(reset_t),
                    "description": desc
                }

            if "Gemini" in d_name:
                parsed_groups["gemini"] = {"displayName": d_name, "buckets": b_map}
                if "weekly" in b_map:
                    gemini_summary["weekly_remaining_fraction"] = b_map["weekly"]["remainingFraction"]
                    gemini_summary["weekly_reset_time"] = b_map["weekly"]["resetTime"]
                    gemini_summary["weekly_reset_human"] = b_map["weekly"]["resetHuman"]
                    gemini_summary["weekly_description"] = b_map["weekly"]["description"]
                if "5h" in b_map:
                    gemini_summary["five_hour_remaining_fraction"] = b_map["5h"]["remainingFraction"]
                    gemini_summary["five_hour_reset_time"] = b_map["5h"]["resetTime"]
                    gemini_summary["five_hour_reset_human"] = b_map["5h"]["resetHuman"]
                    gemini_summary["five_hour_description"] = b_map["5h"]["description"]
            else:
                parsed_groups["thirdParty"] = {"displayName": d_name, "buckets": b_map}
                if "weekly" in b_map:
                    third_party_summary["weekly_remaining_fraction"] = b_map["weekly"]["remainingFraction"]
                    third_party_summary["weekly_reset_time"] = b_map["weekly"]["resetTime"]
                    third_party_summary["weekly_reset_human"] = b_map["weekly"]["resetHuman"]
                    third_party_summary["weekly_description"] = b_map["weekly"]["description"]
                if "5h" in b_map:
                    third_party_summary["five_hour_remaining_fraction"] = b_map["5h"]["remainingFraction"]
                    third_party_summary["five_hour_reset_time"] = b_map["5h"]["resetTime"]
                    third_party_summary["five_hour_reset_human"] = b_map["5h"]["resetHuman"]
                    third_party_summary["five_hour_description"] = b_map["5h"]["description"]

    weekly_frac = gemini_summary["weekly_remaining_fraction"]
    five_h_frac = gemini_summary["five_hour_remaining_fraction"]

    res_dict = {
        "success": True,
        "user": {
            "name": user_name,
            "email": user_email,
            "planName": plan_name,
            "planBadge": plan_badge,
            "tierKey": detected_plan_key,
            "availablePromptCredits": user_status.get("planStatus", {}).get("availablePromptCredits", 0),
            "availableFlowCredits": user_status.get("planStatus", {}).get("availableFlowCredits", 0)
        },
        "gemini": {
            "weeklyPercentRemaining": round(weekly_frac * 100.0, 1),
            "weeklyPercentUsed": round((1.0 - weekly_frac) * 100.0, 1),
            "weeklyResetHuman": gemini_summary["weekly_reset_human"],
            "weeklyResetTime": gemini_summary["weekly_reset_time"],
            "weeklyDescription": gemini_summary["weekly_description"],
            "fiveHourPercentRemaining": round(five_h_frac * 100.0, 1),
            "fiveHourPercentUsed": round((1.0 - five_h_frac) * 100.0, 1),
            "fiveHourResetHuman": gemini_summary["five_hour_reset_human"],
            "fiveHourResetTime": gemini_summary["five_hour_reset_time"],
            "fiveHourDescription": gemini_summary["five_hour_description"]
        },
        "thirdParty": {
            "weeklyPercentRemaining": round(third_party_summary["weekly_remaining_fraction"] * 100.0, 1),
            "weeklyPercentUsed": round((1.0 - third_party_summary["weekly_remaining_fraction"]) * 100.0, 1),
            "weeklyResetHuman": third_party_summary["weekly_reset_human"],
            "weeklyResetTime": third_party_summary["weekly_reset_time"],
            "weeklyDescription": third_party_summary["weekly_description"],
            "fiveHourPercentRemaining": round(third_party_summary["five_hour_remaining_fraction"] * 100.0, 1),
            "fiveHourPercentUsed": round((1.0 - third_party_summary["five_hour_remaining_fraction"]) * 100.0, 1),
            "fiveHourResetHuman": third_party_summary["five_hour_reset_human"],
            "fiveHourResetTime": third_party_summary["five_hour_reset_time"],
            "fiveHourDescription": third_party_summary["five_hour_description"]
        },
        "rawGroups": parsed_groups
    }

    try:
        db.save_user_profile({
            "name": user_name,
            "email": user_email,
            "planName": plan_name,
            "planBadge": plan_badge,
            "weekly_percent": round(weekly_frac * 100.0, 1),
            "five_hour_percent": round(five_h_frac * 100.0, 1),
            "weekly_reset": gemini_summary["weekly_reset_human"],
            "five_hour_reset": gemini_summary["five_hour_reset_human"]
        })
    except Exception:
        pass

    return res_dict

if __name__ == "__main__":
    data = get_quota_summary()
    print(json.dumps(data, indent=2))
