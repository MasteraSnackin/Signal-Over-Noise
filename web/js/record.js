let recording = false;
let mediaRecorder;
let audioChunks = [];

const riskSummaryCopy = {
    low: "Low biomarker concern detected. The tone and pacing suggest a steady response that can remain in the routine follow-up queue.",
    medium: "Medium biomarker concern detected. The response suggests mild strain and may benefit from quicker review by a clinician or care coordinator.",
    high: "High biomarker concern detected. The response should be escalated for prompt review and an active follow-up plan.",
};

const followUpRecommendations = {
    awaiting_response: {
        badge: "Awaiting response",
        badgeClass: "signal-risk risk-awaiting",
        summary: "No voice note has been submitted yet. Prepare a reminder outreach to keep this patient journey moving.",
        days_to_expiry: "1",
        plan_by_campaign: {
            elderly_checkin: "first-response-reminder",
            primary_care: "check-in-reminder",
            mental_health: "wellbeing-response-reminder",
        },
    },
    low: {
        badge: "Routine follow-up",
        badgeClass: "signal-risk risk-low",
        summary: "The latest response looks stable. Prepare a routine follow-up that keeps the care journey warm without escalating urgency.",
        days_to_expiry: "5",
        plan_by_campaign: {
            elderly_checkin: "routine-wellbeing-follow-up",
            primary_care: "routine-clinic-follow-up",
            mental_health: "routine-wellbeing-follow-up",
        },
    },
    medium: {
        badge: "Review soon",
        badgeClass: "signal-risk risk-medium",
        summary: "The latest response suggests some strain. Queue a nearer-term follow-up so the care team can check in again soon.",
        days_to_expiry: "2",
        plan_by_campaign: {
            elderly_checkin: "nurse-review-check-in",
            primary_care: "near-term-clinician-review",
            mental_health: "accelerated-wellbeing-follow-up",
        },
    },
    high: {
        badge: "Priority follow-up",
        badgeClass: "signal-risk risk-high",
        summary: "The latest response warrants a prompt follow-up. Carry this patient back into the outreach console with an urgent plan.",
        days_to_expiry: "0",
        plan_by_campaign: {
            elderly_checkin: "priority-care-team-outreach",
            primary_care: "urgent-clinician-review",
            mental_health: "same-day-wellbeing-follow-up",
        },
    },
};

let latestSignalPayload = null;
let latestHistoryItems = [];
let latestSmsDeliveries = [];
let latestTwilioMessageResource = null;
let latestTwilioMessageList = null;
let latestFallbackHandoffs = [];
let latestReviewStatus = null;
const REVIEWER_NAME_STORAGE_KEY = "signal-over-noise-reviewer-name";
const REVIEW_OUTCOME_STORAGE_KEY = "signal-over-noise-review-outcome";

function waitForDemoStep(milliseconds = 260) {
    return new Promise((resolve) => {
        window.setTimeout(resolve, milliseconds);
    });
}

async function copyTextToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }

    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "readonly");
    textarea.style.position = "absolute";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    document.body.removeChild(textarea);
}

async function buildApiError(response, fallbackMessage) {
    try {
        const payload = await response.clone().json();
        if (payload?.error?.message) {
            return new Error(payload.error.message);
        }
    } catch (error) {
        // Fall back to the caller-provided message.
    }

    return new Error(fallbackMessage);
}

function wireStatusLiveRegions() {
    document.querySelectorAll(".status").forEach((element) => {
        element.setAttribute("role", "status");
        element.setAttribute("aria-live", "polite");
    });
}

function showToast(message, tone = "success") {
    const toastHost = document.getElementById("toast-host");
    if (!toastHost) {
        return;
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${tone}`;
    toast.textContent = message;
    toastHost.appendChild(toast);
    window.setTimeout(() => {
        toast.remove();
    }, 2600);
}

function voiceNoteContext() {
    const status = document.getElementById("status");
    const result = document.getElementById("voice-note-result");
    const recordButton = document.getElementById("record-button");
    const demoNoteButton = document.getElementById("demo-note-button");
    const customerId = document.getElementById("customer_id")?.value;
    const customerName = document.getElementById("customer_name")?.value;
    const campaignType = document.getElementById("campaign_type")?.value;

    return {
        status,
        result,
        recordButton,
        demoNoteButton,
        customerId,
        customerName,
        campaignType,
    };
}

function readStoredReviewerName() {
    try {
        return window.localStorage.getItem(REVIEWER_NAME_STORAGE_KEY) || "";
    } catch (error) {
        return "";
    }
}

function writeStoredReviewerName(value) {
    try {
        if (value) {
            window.localStorage.setItem(REVIEWER_NAME_STORAGE_KEY, value);
        } else {
            window.localStorage.removeItem(REVIEWER_NAME_STORAGE_KEY);
        }
    } catch (error) {
        // Ignore storage errors in demo mode.
    }
}

function prettyOutcome(outcome) {
    return outcome ? outcome.replaceAll("_", " ") : "not set";
}

function readStoredReviewOutcome() {
    try {
        return window.localStorage.getItem(REVIEW_OUTCOME_STORAGE_KEY) || "";
    } catch (error) {
        return "";
    }
}

function writeStoredReviewOutcome(value) {
    try {
        if (value) {
            window.localStorage.setItem(REVIEW_OUTCOME_STORAGE_KEY, value);
        } else {
            window.localStorage.removeItem(REVIEW_OUTCOME_STORAGE_KEY);
        }
    } catch (error) {
        // Ignore storage errors in demo mode.
    }
}

function loadReviewerDraftPreferences() {
    const reviewerNameInput = document.getElementById("reviewer-name");
    const storedReviewerName = readStoredReviewerName();
    if (reviewerNameInput && storedReviewerName) {
        reviewerNameInput.value = storedReviewerName;
    }
    const reviewOutcomeInput = document.getElementById("review-outcome");
    const storedReviewOutcome = readStoredReviewOutcome();
    if (reviewOutcomeInput && storedReviewOutcome) {
        reviewOutcomeInput.value = storedReviewOutcome;
    }
}

function currentReviewDraft() {
    const reviewerNameInput = document.getElementById("reviewer-name");
    const reviewOutcomeInput = document.getElementById("review-outcome");
    const reviewNoteInput = document.getElementById("review-note");
    const reviewedBy = reviewerNameInput?.value?.trim() || "";
    const outcome = reviewOutcomeInput?.value || "routine_followup";
    const note = reviewNoteInput?.value?.trim()
        || (latestSignalPayload?.risk_bucket
            ? `Reviewed after ${latestSignalPayload.risk_bucket} risk signal.`
            : "Reviewed from the patient page.");
    return {
        reviewedBy,
        outcome,
        note,
    };
}

function setVoiceNoteButtonsDisabled(disabled) {
    const { recordButton, demoNoteButton } = voiceNoteContext();
    if (recordButton) {
        recordButton.disabled = disabled;
    }
    if (demoNoteButton) {
        demoNoteButton.disabled = disabled;
    }
}

function formatHistoryTiming(daysToExpiry) {
    if (daysToExpiry === null || daysToExpiry === undefined) {
        return "timing not set";
    }

    return `${daysToExpiry} day${daysToExpiry === 1 ? "" : "s"} to follow-up`;
}

function formatHistoryDate(timestamp) {
    return timestamp ? new Date(timestamp).toLocaleString() : "time unavailable";
}

function comparisonShiftCopy(latest, previous) {
    const changes = [];

    if (latest.plan !== previous.plan) {
        changes.push(`plan moved from ${previous.plan || "follow-up"} to ${latest.plan || "follow-up"}`);
    }

    if (latest.days_to_expiry !== previous.days_to_expiry) {
        changes.push(`timing shifted from ${formatHistoryTiming(previous.days_to_expiry)} to ${formatHistoryTiming(latest.days_to_expiry)}`);
    }

    if (latest.visual_mode !== previous.visual_mode) {
        changes.push(`visual treatment changed from ${previous.visual_mode} visuals to ${latest.visual_mode} visuals`);
    }

    if (latest.script !== previous.script) {
        changes.push("script copy changed to reflect the updated care context");
    }

    if (changes.length === 0) {
        return "The latest outreach kept the same plan, timing, visuals, and script structure.";
    }

    return changes.join("; ");
}

function comparisonSummary(latest, previous) {
    if (latest.plan !== previous.plan && latest.days_to_expiry !== previous.days_to_expiry) {
        return "Both the follow-up plan and the timing changed between the last two outreach attempts.";
    }

    if (latest.plan !== previous.plan) {
        return "The follow-up plan changed between the last two outreach attempts.";
    }

    if (latest.days_to_expiry !== previous.days_to_expiry) {
        return "The follow-up timing changed between the last two outreach attempts.";
    }

    if (latest.visual_mode !== previous.visual_mode) {
        return "The visuals changed between the last two outreach attempts.";
    }

    if (latest.script !== previous.script) {
        return "The script copy changed between the last two outreach attempts.";
    }

    return "The last two outreach attempts are closely aligned, which suggests a stable follow-up approach.";
}

function comparisonRationale(latest, previous, signalPayload, campaignType) {
    const recommendation = followUpRecommendation(signalPayload?.risk_bucket, campaignType);
    const recommendationCopy = `Latest risk is ${signalPayload?.risk_bucket || "not yet available"}, so the recommended next step is ${recommendation.plan} with ${recommendation.days_to_expiry} day${recommendation.days_to_expiry === "1" ? "" : "s"} to follow-up.`;
    const latestTiming = formatHistoryTiming(latest?.days_to_expiry);
    const latestPlan = latest?.plan || "follow-up";
    const latestMatchesRecommendation = Boolean(
        latest
        && latest.plan === recommendation.plan
        && String(latest.days_to_expiry ?? "") === String(recommendation.days_to_expiry)
    );
    const previousMatchesRecommendation = Boolean(
        previous
        && previous.plan === recommendation.plan
        && String(previous.days_to_expiry ?? "") === String(recommendation.days_to_expiry)
    );

    if (!signalPayload?.risk_bucket) {
        if (!latest) {
            return "No voice-note risk signal is available yet, so the comparison can only describe outreach differences, not the biomarker-driven reason behind them.";
        }

        return `${recommendationCopy} Once a voice note is submitted, this note will explain whether the outreach aligns with that signal-driven recommendation.`;
    }

    if (!latest) {
        return recommendationCopy;
    }

    if (latestMatchesRecommendation && previous && !previousMatchesRecommendation) {
        return `${recommendationCopy} The latest outreach moved into alignment with that recommendation from the previous attempt.`;
    }

    if (latestMatchesRecommendation) {
        return `${recommendationCopy} The latest outreach already matches that recommendation.`;
    }

    return `${recommendationCopy} The latest outreach is still using ${latestPlan} with ${latestTiming}.`;
}

function renderOutreachComparison(items) {
    const { campaignType } = voiceNoteContext();
    const compareBadge = document.getElementById("compare-badge");
    const compareSummary = document.getElementById("compare-summary");
    const compareRationale = document.getElementById("compare-rationale");
    const compareLatestPlan = document.getElementById("compare-latest-plan");
    const comparePreviousPlan = document.getElementById("compare-previous-plan");
    const compareShift = document.getElementById("compare-shift");
    const compareLatestMeta = document.getElementById("compare-latest-meta");
    const comparePreviousMeta = document.getElementById("compare-previous-meta");
    const compareLatestScript = document.getElementById("compare-latest-script");
    const comparePreviousScript = document.getElementById("compare-previous-script");

    if (
        !compareBadge || !compareSummary || !compareRationale || !compareLatestPlan || !comparePreviousPlan
        || !compareShift || !compareLatestMeta || !comparePreviousMeta
        || !compareLatestScript || !comparePreviousScript
    ) {
        return;
    }

    if (items.length < 2) {
        compareBadge.className = "signal-risk risk-awaiting";
        compareBadge.textContent = "Awaiting comparison";
        compareSummary.textContent = "Generate at least two outreach attempts for this patient journey to compare how the messaging changes.";
        compareRationale.textContent = comparisonRationale(items[0], null, latestSignalPayload, campaignType);
        compareLatestPlan.textContent = items[0]?.plan || "Only one outreach attempt is available so far.";
        comparePreviousPlan.textContent = "No previous attempt available yet.";
        compareShift.textContent = "Create another outreach attempt to see how the plan, timing, or script evolves.";
        compareLatestMeta.textContent = items[0]
            ? `${formatHistoryDate(items[0].created_at)} · ${items[0].visual_mode} visuals`
            : "No latest script available yet.";
        comparePreviousMeta.textContent = "No previous script available yet.";
        compareLatestScript.textContent = items[0]?.script || "Create another outreach attempt to populate this comparison view.";
        comparePreviousScript.textContent = "Create another outreach attempt to populate this comparison view.";
        return;
    }

    const [latest, previous] = items;
    const changed = latest.plan !== previous.plan
        || latest.days_to_expiry !== previous.days_to_expiry
        || latest.visual_mode !== previous.visual_mode
        || latest.script !== previous.script;

    compareBadge.className = `signal-risk ${changed ? "risk-medium" : "risk-low"}`;
    compareBadge.textContent = changed ? "Messaging updated" : "Messaging steady";
    compareSummary.textContent = comparisonSummary(latest, previous);
    compareRationale.textContent = comparisonRationale(latest, previous, latestSignalPayload, campaignType);
    compareLatestPlan.textContent = `${latest.plan || "follow-up"} · ${formatHistoryTiming(latest.days_to_expiry)}`;
    comparePreviousPlan.textContent = `${previous.plan || "follow-up"} · ${formatHistoryTiming(previous.days_to_expiry)}`;
    compareShift.textContent = comparisonShiftCopy(latest, previous);
    compareLatestMeta.textContent = `${formatHistoryDate(latest.created_at)} · ${latest.visual_mode} visuals · ${latest.job_id}`;
    comparePreviousMeta.textContent = `${formatHistoryDate(previous.created_at)} · ${previous.visual_mode} visuals · ${previous.job_id}`;
    compareLatestScript.textContent = latest.script;
    comparePreviousScript.textContent = previous.script;
}

function renderOutreachHistory(items) {
    const historyStatus = document.getElementById("history-status");
    const historyList = document.getElementById("history-list");

    if (!historyStatus || !historyList) {
        return;
    }

    latestHistoryItems = items;
    historyList.replaceChildren();

    if (items.length === 0) {
        historyStatus.textContent = "No outreach attempts have been generated for this patient journey yet.";
        renderOutreachComparison(items);
        return;
    }

    items.forEach((item, index) => {
        const wrapper = document.createElement("div");
        const top = document.createElement("div");
        const title = document.createElement("strong");
        const modePill = document.createElement("span");
        const meta = document.createElement("div");
        const script = document.createElement("div");
        const assetLink = document.createElement("a");
        const createdAt = new Date(item.created_at).toLocaleString();
        const stageLabel = index === 0 ? "Current outreach" : `Attempt ${items.length - index}`;

        wrapper.className = "history-item";
        top.className = "history-top";
        title.textContent = stageLabel;
        modePill.className = `signal-risk ${item.visual_mode === "uploaded" ? "risk-medium" : "risk-low"}`;
        modePill.textContent = item.visual_mode === "uploaded" ? "uploaded visuals" : "demo visuals";
        meta.className = "history-meta";
        meta.textContent = `${createdAt} · ${item.plan || "follow-up"} · ${formatHistoryTiming(item.days_to_expiry)} · ${item.job_id}`;
        script.className = "history-script";
        script.textContent = item.script;
        assetLink.className = "history-link";
        assetLink.href = item.video_url;
        assetLink.target = "_blank";
        assetLink.rel = "noreferrer";
        assetLink.textContent = "Open generated video asset";

        top.appendChild(title);
        top.appendChild(modePill);
        wrapper.appendChild(top);
        wrapper.appendChild(meta);
        wrapper.appendChild(script);
        wrapper.appendChild(assetLink);
        historyList.appendChild(wrapper);
    });

    historyStatus.textContent = `Showing ${items.length} outreach attempt${items.length === 1 ? "" : "s"} for this journey.`;
    renderOutreachComparison(items);
}

function renderLatestSignal(payload) {
    const signalBadge = document.getElementById("signal-badge");
    const signalSummary = document.getElementById("signal-summary");
    const signalTranscript = document.getElementById("signal-transcript");
    const signalTimestamp = document.getElementById("signal-timestamp");
    const riskBucket = payload?.risk_bucket;

    if (!signalBadge || !signalSummary || !signalTranscript || !signalTimestamp) {
        return;
    }

    latestSignalPayload = payload || null;

    if (!riskBucket) {
        signalBadge.className = "signal-risk risk-awaiting";
        signalBadge.textContent = "Awaiting response";
        signalSummary.textContent = "No voice note has been submitted for this customer yet.";
        signalTranscript.textContent = "No transcript available yet.";
        signalTimestamp.textContent = "No submissions yet.";
        renderOutreachComparison(latestHistoryItems);
        return;
    }

    signalBadge.className = `signal-risk risk-${riskBucket}`;
    signalBadge.textContent = `${riskBucket} risk`;
    signalSummary.textContent = riskSummaryCopy[riskBucket] || "A voice-note response has been received.";
    signalTranscript.textContent = payload.transcript || "Transcript unavailable.";
    signalTimestamp.textContent = payload.created_at
        ? new Date(payload.created_at).toLocaleString()
        : "Timestamp unavailable.";
    renderOutreachComparison(latestHistoryItems);
}

function followUpRecommendation(riskBucket, campaignType) {
    const key = riskBucket || "awaiting_response";
    const recommendation = followUpRecommendations[key] || followUpRecommendations.awaiting_response;
    const plan = recommendation.plan_by_campaign[campaignType] || "follow-up";

    return {
        ...recommendation,
        plan,
    };
}

function buildFollowUpConsoleUrl({
    customerId,
    customerName,
    campaignType,
    plan,
    daysToExpiry,
    riskBucket,
}) {
    const params = new URLSearchParams({
        customer_id: customerId || "",
        name: customerName || "",
        campaign_type: campaignType || "",
        plan,
        days_to_expiry: daysToExpiry,
        source: "patient_page",
    });

    if (riskBucket) {
        params.set("risk_bucket", riskBucket);
    }

    return `/web/upload.html?${params.toString()}`;
}

function renderFollowUpDraft(payload) {
    const { customerId, customerName, campaignType } = voiceNoteContext();
    const followUpBadge = document.getElementById("followup-badge");
    const followUpSummary = document.getElementById("followup-summary");
    const followUpPlan = document.getElementById("followup-plan");
    const followUpTiming = document.getElementById("followup-timing");
    const followUpLink = document.getElementById("followup-link");
    const recommendation = followUpRecommendation(payload?.risk_bucket, campaignType);

    if (!followUpBadge || !followUpSummary || !followUpPlan || !followUpTiming || !followUpLink) {
        return;
    }

    followUpBadge.className = recommendation.badgeClass;
    followUpBadge.textContent = recommendation.badge;
    followUpSummary.textContent = recommendation.summary;
    followUpPlan.textContent = recommendation.plan;
    followUpTiming.textContent = `${recommendation.days_to_expiry} day${recommendation.days_to_expiry === "1" ? "" : "s"} follow-up`;
    followUpLink.href = buildFollowUpConsoleUrl({
        customerId,
        customerName,
        campaignType,
        plan: recommendation.plan,
        daysToExpiry: recommendation.days_to_expiry,
        riskBucket: payload?.risk_bucket || "",
    });
}

function renderSmsDeliveries(payload) {
    const smsBadge = document.getElementById("sms-badge");
    const smsSummary = document.getElementById("sms-summary");
    const smsStatus = document.getElementById("sms-status");
    const smsList = document.getElementById("sms-list");
    const simulateStatusButton = document.getElementById("simulate-sms-status-button");
    const progressStatusButton = document.getElementById("progress-sms-status-button");
    const retryStatusButton = document.getElementById("retry-sms-delivery-button");
    const fallbackLinkButton = document.getElementById("prepare-sms-fallback-button");

    if (!smsBadge || !smsSummary || !smsStatus || !smsList) {
        return;
    }

    const deliveries = payload?.deliveries || [];
    latestSmsDeliveries = deliveries;
    smsList.replaceChildren();

    if (deliveries.length === 0) {
        smsBadge.className = "signal-risk risk-awaiting";
        smsBadge.textContent = "No SMS sent";
        smsSummary.textContent = "Send the current care-page link as a Twilio-style demo SMS so the outreach handoff is visible from the patient context too.";
        smsStatus.textContent = "No patient-level Twilio outreach sent yet.";
        if (simulateStatusButton) {
            simulateStatusButton.disabled = true;
        }
        if (progressStatusButton) {
            progressStatusButton.disabled = true;
        }
        if (retryStatusButton) {
            retryStatusButton.disabled = true;
        }
        if (fallbackLinkButton) {
            fallbackLinkButton.disabled = true;
        }
        renderTwilioMessageResource(null);
        renderTwilioMessageList(null);
        return;
    }

    if (simulateStatusButton) {
        simulateStatusButton.disabled = false;
    }
    if (progressStatusButton) {
        progressStatusButton.disabled = false;
    }
    if (retryStatusButton) {
        retryStatusButton.disabled = !["failed", "undelivered"].includes(deliveries[0].status);
    }
    if (fallbackLinkButton) {
        fallbackLinkButton.disabled = !["failed", "undelivered"].includes(deliveries[0].status);
    }
    const latest = deliveries[0];
    smsBadge.className = latest.status === "delivered" ? "signal-risk risk-low" : "signal-risk risk-medium";
    smsBadge.textContent = `${latest.provider} ${latest.status}`;
    smsSummary.textContent = `Latest Twilio-style outreach went to ${latest.destination} and used the current patient journey link.`;
    smsStatus.textContent = `Showing ${deliveries.length} patient-context SMS outreach attempt${deliveries.length === 1 ? "" : "s"}.`;

    deliveries.forEach((delivery) => {
        const wrapper = document.createElement("div");
        const top = document.createElement("div");
        const heading = document.createElement("strong");
        const pill = document.createElement("span");
        const meta = document.createElement("div");
        const copy = document.createElement("div");

        wrapper.className = "sms-item";
        top.className = "sms-top";
        heading.textContent = `${new Date(delivery.created_at).toLocaleString()} · ${delivery.destination}`;
        pill.className = "signal-risk risk-medium";
        if (delivery.status === "delivered") {
            pill.className = "signal-risk risk-low";
        }
        pill.textContent = `${delivery.provider} ${delivery.status}`;
        meta.className = "history-meta";
        meta.textContent = [
            delivery.provider_message_id,
            delivery.channel,
            delivery.from_number ? `from ${delivery.from_number}` : null,
            delivery.messaging_service_sid ? `service ${delivery.messaging_service_sid}` : null,
        ].filter(Boolean).join(" · ");
        copy.className = "sms-copy";
        copy.textContent = delivery.message_body;

        top.appendChild(heading);
        top.appendChild(pill);
        wrapper.appendChild(top);
        wrapper.appendChild(meta);
        wrapper.appendChild(copy);
        smsList.appendChild(wrapper);
    });

    loadTwilioMessageResource(latest.provider_message_id);
    loadTwilioMessageList();
}

function renderTwilioMessageResource(payload) {
    const resourceStatus = document.getElementById("sms-resource-status");
    const resourcePreview = document.getElementById("sms-resource-preview");

    if (!resourceStatus || !resourcePreview) {
        return;
    }

    latestTwilioMessageResource = payload || null;

    if (!payload) {
        resourceStatus.textContent = "No Twilio message resource loaded yet.";
        resourcePreview.textContent = "No Twilio message resource loaded yet.";
        return;
    }

    resourceStatus.textContent = `Showing Twilio-style Message resource ${payload.sid}.`;
    resourcePreview.textContent = JSON.stringify(payload, null, 2);
}

function renderTwilioMessageList(payload) {
    const listStatus = document.getElementById("sms-message-list-status");
    const listPreview = document.getElementById("sms-message-list-preview");

    if (!listStatus || !listPreview) {
        return;
    }

    latestTwilioMessageList = payload || null;

    if (!payload) {
        listStatus.textContent = "No Twilio message list loaded yet.";
        listPreview.textContent = "No Twilio message list loaded yet.";
        return;
    }

    listStatus.textContent = payload.messages.length === 0
        ? "No Twilio message resources are available for this patient journey."
        : `Showing ${payload.messages.length} Twilio-style message resource${payload.messages.length === 1 ? "" : "s"} for this patient journey.`;
    listPreview.textContent = JSON.stringify(payload, null, 2);
}

function renderFallbackHandoffs(payload) {
    const fallbackStatus = document.getElementById("sms-fallback-status");
    const fallbackList = document.getElementById("sms-fallback-list");

    if (!fallbackStatus || !fallbackList) {
        return;
    }

    const handoffs = payload?.handoffs || [];
    latestFallbackHandoffs = handoffs;
    fallbackList.replaceChildren();

    if (handoffs.length === 0) {
        fallbackStatus.textContent = "No secure-link fallbacks prepared yet.";
        return;
    }

    fallbackStatus.textContent = `Showing ${handoffs.length} secure-link fallback handoff${handoffs.length === 1 ? "" : "s"} for this patient journey.`;
    handoffs.forEach((handoff, index) => {
        const wrapper = document.createElement("div");
        const top = document.createElement("div");
        const heading = document.createElement("strong");
        const pill = document.createElement("span");
        const meta = document.createElement("div");
        const link = document.createElement("a");

        wrapper.className = "sms-item";
        top.className = "sms-top";
        heading.textContent = `${index === 0 ? "Latest fallback" : `Earlier fallback ${index}`} · ${new Date(handoff.created_at).toLocaleString()}`;
        pill.className = "signal-risk risk-medium";
        pill.textContent = `secure link ${handoff.delivery_status || "prepared"}`;
        meta.className = "history-meta";
        meta.textContent = [
            handoff.message_sid || "manual handoff",
            handoff.source ? handoff.source.replaceAll("_", " ") : null,
        ].filter(Boolean).join(" · ");
        link.href = handoff.absolute_page_url;
        link.textContent = handoff.absolute_page_url;

        top.appendChild(heading);
        top.appendChild(pill);
        wrapper.appendChild(top);
        wrapper.appendChild(meta);
        wrapper.appendChild(link);
        fallbackList.appendChild(wrapper);
    });
}

function renderVoiceNotePayload(payload) {
    const { status, result } = voiceNoteContext();
    if (status) {
        status.textContent = `Voice note submitted. Risk bucket: ${payload.risk_bucket}.`;
    }
    showToast(`Voice note submitted with ${payload.risk_bucket} risk.`, "success");
    if (result) {
        result.textContent = JSON.stringify(payload, null, 2);
    }
    renderLatestSignal(payload);
    renderFollowUpDraft(payload);
}

function renderReviewStatus(payload) {
    const reviewBadge = document.getElementById("review-badge");
    const reviewSummary = document.getElementById("review-summary");
    const reviewMeta = document.getElementById("review-meta");
    const reviewStatus = document.getElementById("review-status");
    const markReviewedButton = document.getElementById("mark-reviewed-button");

    if (!reviewBadge || !reviewSummary || !reviewMeta || !reviewStatus || !markReviewedButton) {
        return;
    }

    latestReviewStatus = payload || null;
    const review = payload?.review || null;
    const caseStatus = payload?.status || "awaiting_response";

    if (!review) {
        reviewBadge.className = "signal-risk risk-awaiting";
        reviewBadge.textContent = caseStatus === "awaiting_response" ? "Awaiting voice note" : "Open case";
        reviewSummary.textContent = caseStatus === "awaiting_response"
            ? "A voice note must be submitted before the care team can mark this case reviewed."
            : "This case has not been marked reviewed yet. When the care team acknowledges it, that closure will show up here and in the care-ops queue.";
        reviewMeta.textContent = "No review activity recorded yet.";
        reviewStatus.textContent = caseStatus === "awaiting_response"
            ? "Submit or demo a voice note before marking this case reviewed."
            : "No patient-level review action recorded yet.";
        markReviewedButton.disabled = caseStatus === "awaiting_response";
        markReviewedButton.textContent = caseStatus === "awaiting_response" ? "Awaiting voice note" : "Mark case reviewed";
        return;
    }

    reviewMeta.textContent = `${new Date(review.reviewed_at).toLocaleString()} · ${review.reviewed_by} · outcome ${prettyOutcome(review.outcome)}${review.note ? ` · ${review.note}` : ""}`;

    if (review.active) {
        reviewBadge.className = "signal-risk risk-reviewed";
        reviewBadge.textContent = "Reviewed";
        reviewSummary.textContent = `The care team has already acknowledged this case with a ${prettyOutcome(review.outcome)} outcome. A newer signal will automatically reopen it if the patient submits another response.`;
        reviewStatus.textContent = "Latest review is still active.";
        markReviewedButton.disabled = true;
        markReviewedButton.textContent = "Already reviewed";
        return;
    }

    reviewBadge.className = "signal-risk risk-medium";
    reviewBadge.textContent = "Review reopened";
    reviewSummary.textContent = "This case was reviewed previously, but a newer voice signal has reopened it for the care team.";
    reviewStatus.textContent = "A newer signal arrived after the last review, so the case is open again.";
    markReviewedButton.disabled = false;
    markReviewedButton.textContent = "Mark case reviewed again";
}

function buildCaseBrief() {
    const { customerId, customerName, campaignType } = voiceNoteContext();
    const signalBadge = document.getElementById("signal-badge")?.textContent?.trim() || "Awaiting response";
    const signalSummary = document.getElementById("signal-summary")?.textContent?.trim() || "No signal summary available.";
    const signalTranscript = document.getElementById("signal-transcript")?.textContent?.trim() || "No transcript available yet.";
    const signalTimestamp = document.getElementById("signal-timestamp")?.textContent?.trim() || "No submissions yet.";
    const followUpBadge = document.getElementById("followup-badge")?.textContent?.trim() || "Awaiting response";
    const followUpSummary = document.getElementById("followup-summary")?.textContent?.trim() || "No follow-up summary available.";
    const followUpPlan = document.getElementById("followup-plan")?.textContent?.trim() || "follow-up";
    const followUpTiming = document.getElementById("followup-timing")?.textContent?.trim() || "timing not set";
    const compareBadge = document.getElementById("compare-badge")?.textContent?.trim() || "Awaiting comparison";
    const compareSummary = document.getElementById("compare-summary")?.textContent?.trim() || "No comparison summary available.";
    const compareRationale = document.getElementById("compare-rationale")?.textContent?.trim() || "No risk-linked explanation is available yet.";
    const smsBadge = document.getElementById("sms-badge")?.textContent?.trim() || "No SMS sent";
    const smsSummary = document.getElementById("sms-summary")?.textContent?.trim() || "No Twilio summary available.";
    const reviewBadge = document.getElementById("review-badge")?.textContent?.trim() || "Open case";
    const reviewSummary = document.getElementById("review-summary")?.textContent?.trim() || "No review summary available.";
    const reviewMeta = document.getElementById("review-meta")?.textContent?.trim() || "No review activity recorded yet.";
    const reviewDraft = currentReviewDraft();
    const now = new Date();

    const historyLines = latestHistoryItems.length === 0
        ? ["- No outreach attempts have been generated for this patient journey yet."]
        : latestHistoryItems.map((item, index) => {
            const stageLabel = index === 0 ? "Current outreach" : `Prior attempt ${index}`;
            return `- ${stageLabel}: ${formatHistoryDate(item.created_at)} · ${item.plan || "follow-up"} · ${formatHistoryTiming(item.days_to_expiry)} · ${item.visual_mode} visuals · ${item.job_id}`;
        });

    const smsLines = latestSmsDeliveries.length === 0
        ? ["- No Twilio-style SMS deliveries have been sent from this patient page yet."]
        : latestSmsDeliveries.map((delivery, index) => {
            const prefix = index === 0 ? "Latest SMS" : `Earlier SMS ${index}`;
            return [
                `- ${prefix}: ${new Date(delivery.created_at).toLocaleString()}`,
                `${delivery.provider} ${delivery.status}`,
                delivery.destination,
                delivery.from_number ? `from ${delivery.from_number}` : null,
                delivery.messaging_service_sid ? `service ${delivery.messaging_service_sid}` : null,
                delivery.provider_message_id,
            ].filter(Boolean).join(" · ");
        });
    const twilioResourceLines = latestTwilioMessageResource
        ? [
            `- SID: ${latestTwilioMessageResource.sid}`,
            `- Status: ${latestTwilioMessageResource.status}`,
            `- To: ${latestTwilioMessageResource.to}`,
            `- From: ${latestTwilioMessageResource.from || latestTwilioMessageResource.from_number || "not set"}`,
            `- Messaging Service: ${latestTwilioMessageResource.messaging_service_sid || "not set"}`,
            `- Resource URI: ${latestTwilioMessageResource.uri}`,
        ]
        : ["- No Twilio message resource has been loaded yet."];
    const twilioMessageListLines = latestTwilioMessageList
        ? [
            `- Message list URI: ${latestTwilioMessageList.uri}`,
            `- Message count: ${latestTwilioMessageList.messages.length}`,
        ]
        : ["- No Twilio message list has been loaded yet."];
    const fallbackLines = latestFallbackHandoffs.length === 0
        ? ["- No secure-link fallbacks have been prepared for this patient journey yet."]
        : latestFallbackHandoffs.map((handoff, index) => {
            const prefix = index === 0 ? "Latest fallback" : `Earlier fallback ${index}`;
            return [
                `- ${prefix}: ${new Date(handoff.created_at).toLocaleString()}`,
                handoff.delivery_status ? `after ${handoff.delivery_status}` : "manual fallback",
                handoff.message_sid || "no message SID",
                handoff.absolute_page_url,
            ].join(" · ");
        });

    return [
        "Signal Over Noise Case Brief",
        `Generated: ${now.toLocaleString()}`,
        `Source: ${window.location.href}`,
        "",
        "Patient Context",
        `- Customer ID: ${customerId || "unknown"}`,
        `- Customer name: ${customerName || "unknown"}`,
        `- Campaign: ${(campaignType || "unknown").replaceAll("_", " ")}`,
        "",
        "Latest Signal",
        `- Badge: ${signalBadge}`,
        `- Summary: ${signalSummary}`,
        `- Transcript: ${signalTranscript}`,
        `- Last updated: ${signalTimestamp}`,
        "",
        "Recommended Follow-up",
        `- Badge: ${followUpBadge}`,
        `- Summary: ${followUpSummary}`,
        `- Plan: ${followUpPlan}`,
        `- Timing: ${followUpTiming}`,
        "",
        "Messaging Shift",
        `- Badge: ${compareBadge}`,
        `- Summary: ${compareSummary}`,
        `- Rationale: ${compareRationale}`,
        "",
        "Outreach History",
        ...historyLines,
        "",
        "Twilio Follow-up",
        `- Badge: ${smsBadge}`,
        `- Summary: ${smsSummary}`,
        ...smsLines,
        ...twilioResourceLines,
        ...twilioMessageListLines,
        ...fallbackLines,
        "",
        "Review State",
        `- Badge: ${reviewBadge}`,
        `- Summary: ${reviewSummary}`,
        `- Latest review: ${reviewMeta}`,
        `- Reviewer draft: ${reviewDraft.reviewedBy || "not set"}`,
        `- Review outcome draft: ${prettyOutcome(reviewDraft.outcome)}`,
        `- Review note draft: ${reviewDraft.note || "not set"}`,
    ].join("\n");
}

function downloadCaseBrief() {
    const { customerId, campaignType, status } = voiceNoteContext();
    const brief = buildCaseBrief();
    const customerLabel = (customerId || "case").replace(/[^a-zA-Z0-9_-]/g, "-");
    const campaignLabel = (campaignType || "campaign").replace(/[^a-zA-Z0-9_-]/g, "-");
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const blob = new Blob([brief], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = url;
    link.download = `signal-over-noise-case-brief-${customerLabel}-${campaignLabel}-${timestamp}.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    if (status) {
        status.textContent = "Case brief downloaded from the current patient-page state.";
    }
    showToast("Case brief downloaded.", "success");
}

async function loadSmsDeliveries() {
    const { customerId, campaignType } = voiceNoteContext();
    const smsStatus = document.getElementById("sms-status");

    try {
        const params = new URLSearchParams({
            customer_id: customerId || "",
            campaign_type: campaignType || "",
            limit: "6",
        });
        const response = await fetch(`/api/v1/video/outreach_deliveries?${params.toString()}`);

        if (!response.ok) {
            throw new Error("Could not load Twilio delivery history.");
        }

        renderSmsDeliveries(await response.json());
    } catch (error) {
        if (smsStatus) {
            smsStatus.textContent = error.message;
        }
    }
}

async function loadTwilioMessageResource(messageSid) {
    const resourceStatus = document.getElementById("sms-resource-status");

    if (!messageSid) {
        renderTwilioMessageResource(null);
        return;
    }

    try {
        const params = new URLSearchParams({ message_sid: messageSid });
        const response = await fetch(`/api/v1/video/twilio_message?${params.toString()}`);

        if (!response.ok) {
            throw new Error("Could not load the Twilio message resource.");
        }

        renderTwilioMessageResource(await response.json());
    } catch (error) {
        if (resourceStatus) {
            resourceStatus.textContent = error.message;
        }
    }
}

async function loadTwilioMessageList() {
    const { customerId, campaignType } = voiceNoteContext();
    const listStatus = document.getElementById("sms-message-list-status");

    try {
        const params = new URLSearchParams({
            customer_id: customerId || "",
            campaign_type: campaignType || "",
            limit: "6",
        });
        const response = await fetch(`/api/v1/video/twilio_messages?${params.toString()}`);

        if (!response.ok) {
            throw new Error("Could not load the Twilio message list.");
        }

        renderTwilioMessageList(await response.json());
    } catch (error) {
        if (listStatus) {
            listStatus.textContent = error.message;
        }
    }
}

async function loadFallbackHandoffs() {
    const { customerId, campaignType } = voiceNoteContext();
    const fallbackStatus = document.getElementById("sms-fallback-status");

    try {
        const params = new URLSearchParams({
            customer_id: customerId || "",
            campaign_type: campaignType || "",
            limit: "6",
        });
        const response = await fetch(`/api/v1/video/fallback_handoffs?${params.toString()}`);

        if (!response.ok) {
            throw new Error("Could not load secure-link fallback history.");
        }

        renderFallbackHandoffs(await response.json());
    } catch (error) {
        if (fallbackStatus) {
            fallbackStatus.textContent = error.message;
        }
    }
}

async function loadReviewStatus() {
    const { customerId, campaignType } = voiceNoteContext();
    const reviewStatus = document.getElementById("review-status");

    try {
        const params = new URLSearchParams({
            customer_id: customerId || "",
            campaign_type: campaignType || "",
        });
        const response = await fetch(`/api/v1/video/review_status?${params.toString()}`);

        if (!response.ok) {
            throw new Error("Could not load review status.");
        }

        renderReviewStatus(await response.json());
    } catch (error) {
        if (reviewStatus) {
            reviewStatus.textContent = error.message;
        }
    }
}

async function loadLatestSignal() {
    const { customerId, campaignType, status } = voiceNoteContext();

    try {
        const params = new URLSearchParams({
            customer_id: customerId || "",
            campaign_type: campaignType || "",
        });
        const response = await fetch(`/api/v1/voice_note/latest?${params.toString()}`);

        if (response.status === 404) {
            renderLatestSignal(null);
            renderFollowUpDraft(null);
            return;
        }

        if (!response.ok) {
            throw new Error("Could not load the latest signal snapshot.");
        }

        const payload = await response.json();
        renderLatestSignal(payload);
        renderFollowUpDraft(payload);
    } catch (error) {
        if (status) {
            status.textContent = error.message;
        }
    }
}

async function loadOutreachHistory() {
    const { customerId, campaignType, status } = voiceNoteContext();

    try {
        const params = new URLSearchParams({
            customer_id: customerId || "",
            campaign_type: campaignType || "",
            limit: "6",
        });
        const response = await fetch(`/api/v1/video/history?${params.toString()}`);

        if (!response.ok) {
            throw new Error("Could not load outreach history.");
        }

        renderOutreachHistory(await response.json());
    } catch (error) {
        const historyStatus = document.getElementById("history-status");
        if (historyStatus) {
            historyStatus.textContent = error.message;
        } else if (status) {
            status.textContent = error.message;
        }
    }
}

async function sendPatientSmsFollowUp() {
    const { customerId, campaignType, customerName, status } = voiceNoteContext();
    const smsPhoneNumber = document.getElementById("sms-phone-number");
    const smsStatus = document.getElementById("sms-status");
    const sendSmsButton = document.getElementById("send-sms-button");
    const phoneNumber = smsPhoneNumber?.value?.trim() || "";

    if (!phoneNumber) {
        if (smsStatus) {
            smsStatus.textContent = "Enter a phone number before sending the Twilio demo SMS.";
        }
        return;
    }

    if (sendSmsButton) {
        sendSmsButton.disabled = true;
    }
    if (smsStatus) {
        smsStatus.textContent = `Queueing a Twilio-style SMS for ${customerName || customerId || "this patient"}...`;
    }

    try {
        const response = await fetch("/api/v1/video/send_outreach", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                customer_id: customerId || "",
                campaign_type: campaignType || "",
                phone_number: phoneNumber,
            }),
        });

        if (!response.ok) {
            throw await buildApiError(response, "Could not send the patient-level Twilio demo SMS.");
        }

        const payload = await response.json();
        if (status) {
            status.textContent = `Twilio demo SMS queued for ${payload.delivery.destination}.`;
        }
        showToast("Twilio demo SMS queued.", "success");
        await loadSmsDeliveries();
    } catch (error) {
        if (smsStatus) {
            smsStatus.textContent = error.message;
        }
        showToast(error.message, "error");
    } finally {
        if (sendSmsButton) {
            sendSmsButton.disabled = false;
        }
    }
}

async function simulatePatientSmsStatus() {
    const smsStatus = document.getElementById("sms-status");
    const simulateStatusButton = document.getElementById("simulate-sms-status-button");
    const simStatusSelect = document.getElementById("sms-sim-status");
    const latestMessageSid = latestTwilioMessageResource?.sid || latestSmsDeliveries[0]?.provider_message_id;
    const nextStatus = simStatusSelect?.value || "delivered";

    if (!latestMessageSid) {
        if (smsStatus) {
            smsStatus.textContent = "Send a Twilio demo SMS before simulating a status callback.";
        }
        return;
    }

    if (simulateStatusButton) {
        simulateStatusButton.disabled = true;
    }
    if (smsStatus) {
        smsStatus.textContent = `Simulating Twilio ${nextStatus} for ${latestMessageSid}...`;
    }

    try {
        const response = await fetch("/api/v1/video/twilio_simulate_status", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                message_sid: latestMessageSid,
                status: nextStatus,
            }),
        });

        if (!response.ok) {
            throw await buildApiError(response, "Could not simulate the patient-level Twilio status callback.");
        }

        await response.json();
        await loadSmsDeliveries();
        if (smsStatus) {
            smsStatus.textContent = `Twilio message ${latestMessageSid} moved to ${nextStatus}.`;
        }
    } catch (error) {
        if (smsStatus) {
            smsStatus.textContent = error.message;
        }
    } finally {
        if (simulateStatusButton) {
            simulateStatusButton.disabled = false;
        }
    }
}

async function progressPatientSmsStatus() {
    const smsStatus = document.getElementById("sms-status");
    const simulateStatusButton = document.getElementById("simulate-sms-status-button");
    const progressStatusButton = document.getElementById("progress-sms-status-button");
    const simStatusSelect = document.getElementById("sms-sim-status");
    const latestMessageSid = latestTwilioMessageResource?.sid || latestSmsDeliveries[0]?.provider_message_id;
    const lifecycle = ["sent", "delivered"];

    if (!latestMessageSid) {
        if (smsStatus) {
            smsStatus.textContent = "Send a Twilio demo SMS before running the lifecycle demo.";
        }
        return;
    }

    if (simulateStatusButton) {
        simulateStatusButton.disabled = true;
    }
    if (progressStatusButton) {
        progressStatusButton.disabled = true;
    }
    if (smsStatus) {
        smsStatus.textContent = `Running Twilio lifecycle demo for ${latestMessageSid}...`;
    }

    try {
        for (const nextStatus of lifecycle) {
            const response = await fetch("/api/v1/video/twilio_simulate_status", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    message_sid: latestMessageSid,
                    status: nextStatus,
                }),
            });

            if (!response.ok) {
                throw await buildApiError(response, "Could not run the patient-level Twilio lifecycle demo.");
            }

            if (simStatusSelect) {
                simStatusSelect.value = nextStatus;
            }
            await response.json();
            await loadSmsDeliveries();
            if (nextStatus !== lifecycle[lifecycle.length - 1]) {
                await waitForDemoStep();
            }
        }

        if (smsStatus) {
            smsStatus.textContent = `Twilio message ${latestMessageSid} progressed from queued to delivered.`;
        }
    } catch (error) {
        if (smsStatus) {
            smsStatus.textContent = error.message;
        }
    } finally {
        if (simulateStatusButton) {
            simulateStatusButton.disabled = false;
        }
        if (progressStatusButton) {
            progressStatusButton.disabled = false;
        }
    }
}

async function retryPatientSmsDelivery() {
    const smsStatus = document.getElementById("sms-status");
    const retryStatusButton = document.getElementById("retry-sms-delivery-button");
    const latestDelivery = latestSmsDeliveries[0] || null;

    if (!latestDelivery) {
        if (smsStatus) {
            smsStatus.textContent = "Send a Twilio demo SMS before retrying a failed delivery.";
        }
        return;
    }

    if (!["failed", "undelivered"].includes(latestDelivery.status)) {
        if (smsStatus) {
            smsStatus.textContent = `Twilio message ${latestDelivery.provider_message_id} is ${latestDelivery.status}. Simulate a failed or undelivered status before retrying it.`;
        }
        return;
    }

    if (retryStatusButton) {
        retryStatusButton.disabled = true;
    }
    if (smsStatus) {
        smsStatus.textContent = `Retrying failed Twilio delivery ${latestDelivery.provider_message_id}...`;
    }

    try {
        const response = await fetch("/api/v1/video/retry_outreach", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                message_sid: latestDelivery.provider_message_id,
            }),
        });

        if (!response.ok) {
            throw await buildApiError(response, "Could not retry the patient-level failed Twilio delivery.");
        }

        const payload = await response.json();
        await loadSmsDeliveries();
        if (smsStatus) {
            smsStatus.textContent = `Retried Twilio delivery. New message ${payload.retried_delivery.provider_message_id} is queued.`;
        }
    } catch (error) {
        if (smsStatus) {
            smsStatus.textContent = error.message;
        }
    } finally {
        if (retryStatusButton) {
            retryStatusButton.disabled = false;
        }
    }
}

async function preparePatientSmsFallback() {
    const { customerId, campaignType, customerName } = voiceNoteContext();
    const smsStatus = document.getElementById("sms-status");
    const fallbackLinkButton = document.getElementById("prepare-sms-fallback-button");
    const latestDelivery = latestSmsDeliveries[0] || null;

    if (!latestDelivery) {
        if (smsStatus) {
            smsStatus.textContent = "Send a Twilio demo SMS before preparing a secure-link fallback.";
        }
        return;
    }

    if (!["failed", "undelivered"].includes(latestDelivery.status)) {
        if (smsStatus) {
            smsStatus.textContent = `Twilio message ${latestDelivery.provider_message_id} is ${latestDelivery.status}. Simulate a failed or undelivered status before preparing the secure-link fallback.`;
        }
        return;
    }

    if (fallbackLinkButton) {
        fallbackLinkButton.disabled = true;
    }
    if (smsStatus) {
        smsStatus.textContent = `Preparing a secure-link fallback for ${customerName || customerId || "this patient"}...`;
    }

    try {
        const response = await fetch("/api/v1/video/prepare_fallback_link", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                customer_id: customerId || "",
                campaign_type: campaignType || "",
                message_sid: latestDelivery.provider_message_id,
                source: "patient_page_twilio_failover",
            }),
        });

        if (!response.ok) {
            throw await buildApiError(response, "Could not prepare the secure-link fallback from the patient page.");
        }

        const payload = await response.json();
        await copyTextToClipboard(payload.absolute_page_url);
        await loadFallbackHandoffs();
        if (smsStatus) {
            smsStatus.textContent = `Secure-link fallback copied for ${payload.name}. The Twilio failover was logged for message ${payload.message_sid}.`;
        }
        showToast(`Secure-link fallback copied for ${payload.name}.`, "success");
    } catch (error) {
        if (smsStatus) {
            smsStatus.textContent = error.message;
        }
        showToast(error.message, "error");
    } finally {
        if (fallbackLinkButton) {
            fallbackLinkButton.disabled = false;
        }
    }
}

async function markCurrentCaseReviewed() {
    const { customerId, campaignType, status } = voiceNoteContext();
    const reviewStatus = document.getElementById("review-status");
    const markReviewedButton = document.getElementById("mark-reviewed-button");
    const draft = currentReviewDraft();

    if (latestReviewStatus?.status === "awaiting_response") {
        if (reviewStatus) {
            reviewStatus.textContent = "Submit or demo a voice note before marking this case reviewed.";
        }
        return;
    }

    if (!draft.reviewedBy) {
        if (reviewStatus) {
            reviewStatus.textContent = "Add a reviewer name before marking this case reviewed.";
        }
        document.getElementById("reviewer-name")?.focus();
        return;
    }

    if (markReviewedButton) {
        markReviewedButton.disabled = true;
    }
    if (reviewStatus) {
        reviewStatus.textContent = "Marking this case as reviewed...";
    }
    writeStoredReviewerName(draft.reviewedBy);
    writeStoredReviewOutcome(draft.outcome);

    try {
        const response = await fetch("/api/v1/video/mark_reviewed", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                customer_id: customerId || "",
                campaign_type: campaignType || "",
                reviewed_by: draft.reviewedBy,
                outcome: draft.outcome,
                source: "patient_page",
                note: draft.note,
            }),
        });

        if (!response.ok) {
            throw await buildApiError(response, "Could not mark this case as reviewed.");
        }

        await loadReviewStatus();
        if (status) {
            status.textContent = "Case marked reviewed from the patient page.";
        }
        showToast("Case marked reviewed.", "success");
    } catch (error) {
        if (reviewStatus) {
            reviewStatus.textContent = error.message;
        }
        showToast(error.message, "error");
    } finally {
        if (markReviewedButton) {
            const awaitingResponse = latestReviewStatus?.status === "awaiting_response";
            const activeReview = Boolean(latestReviewStatus?.review?.active);
            markReviewedButton.disabled = awaitingResponse || activeReview;
        }
    }
}

async function submitDemoVoiceNote() {
    const { status, result, customerId, campaignType } = voiceNoteContext();

    status.textContent = "Submitting a demo voice note...";
    if (result) {
        result.textContent = "";
    }
    setVoiceNoteButtonsDisabled(true);

    try {
        const response = await fetch("/api/v1/voice_note/mock_submit", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                customer_id: customerId || "",
                campaign_type: campaignType || "",
            }),
        });

        if (!response.ok) {
            throw await buildApiError(response, "Demo voice note submission failed.");
        }

        renderVoiceNotePayload(await response.json());
        await loadReviewStatus();
    } catch (error) {
        status.textContent = error.message;
    } finally {
        setVoiceNoteButtonsDisabled(false);
    }
}

async function startRecording() {
    const { status, result, customerId, campaignType } = voiceNoteContext();

    if (recording) {
        status.textContent = "Recording already in progress.";
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

        status.textContent = "Recording voice note for 15 seconds...";
        if (result) {
            result.textContent = "";
        }
        recording = true;
        setVoiceNoteButtonsDisabled(true);
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream);

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };

        mediaRecorder.onstop = async () => {
            const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
            const formData = new FormData();
            formData.append("customer_id", customerId || "");
            formData.append("campaign_type", campaignType || "");
            formData.append("audio", audioBlob, "voice_note.webm");

            try {
                const response = await fetch("/api/v1/voice_note/submit", {
                    method: "POST",
                    body: formData,
                });

                if (!response.ok) {
                    throw await buildApiError(response, "Voice note submission failed.");
                }

                renderVoiceNotePayload(await response.json());
                await loadReviewStatus();
            } catch (error) {
                status.textContent = error.message;
            } finally {
                stream.getTracks().forEach((track) => track.stop());
                recording = false;
                setVoiceNoteButtonsDisabled(false);
            }
        };

        mediaRecorder.start();
        window.setTimeout(() => {
            if (mediaRecorder && mediaRecorder.state !== "inactive") {
                mediaRecorder.stop();
            }
        }, 15000);
    } catch (error) {
        recording = false;
        setVoiceNoteButtonsDisabled(false);
        status.textContent = `Microphone access failed: ${error.message}`;
    }
}

renderFollowUpDraft(null);
wireStatusLiveRegions();
loadReviewerDraftPreferences();
loadLatestSignal();
loadOutreachHistory();
loadSmsDeliveries();
loadFallbackHandoffs();
loadReviewStatus();

document.getElementById("send-sms-button")?.addEventListener("click", sendPatientSmsFollowUp);
document.getElementById("simulate-sms-status-button")?.addEventListener("click", simulatePatientSmsStatus);
document.getElementById("progress-sms-status-button")?.addEventListener("click", progressPatientSmsStatus);
document.getElementById("retry-sms-delivery-button")?.addEventListener("click", retryPatientSmsDelivery);
document.getElementById("prepare-sms-fallback-button")?.addEventListener("click", preparePatientSmsFallback);
document.getElementById("download-case-brief-button")?.addEventListener("click", downloadCaseBrief);
document.getElementById("mark-reviewed-button")?.addEventListener("click", markCurrentCaseReviewed);
document.getElementById("reviewer-name")?.addEventListener("input", (event) => {
    writeStoredReviewerName(event.target.value.trim());
});
document.getElementById("review-outcome")?.addEventListener("change", (event) => {
    writeStoredReviewOutcome(event.target.value);
});
