# AI Usage Monitoring

Track site-wide AI usage and health in one place: calls that users make with their own keys through the AI API, usage of the platform's built-in AI features (template recommendations, AI navigation, Teacher Judge and others), and the runtime status of the AI Gateway and each model. Sidebar: Monitoring & Logs → AI Usage Monitoring. Path: `/ai-monitoring`.

## Who can use it

Administrators only. Other roles do not see the page in the sidebar.

## Before you start

- Model runtime status comes from the AI Gateway (LiteLLM). If the gateway cannot be reached, usage figures still load but the runtime columns stay empty and a problem is listed under **System status**.
- The **View details in Grafana** button only appears when the optional monitoring stack is enabled.

## Step by step

### Choose the time range

1. Use **Last 7 days**, **Last 30 days** or **Last 90 days** at the top right.
2. The summary, the trend chart and the call details below all change together.
3. **Last updated** next to the switch shows when the data was last loaded. The page also refreshes on its own.

![Choose the time range (numbers match the steps above)](/manual-shots/en/ai-monitoring/s01.webp)

### Read the summary metrics

- **Call Count**: total calls in the period. The line below compares it with the previous period of the same length, as a percentage.
- **Total Tokens**: tokens used in the selected period only.
- **Error rate**: share of failed calls. The line below shows the change from the previous period in percentage points (pp); a rise is shown in red.

![Read the summary metrics](/manual-shots/en/ai-monitoring/s02.webp)

### Read the trend chart

1. In **Call and error trend**, choose what to plot: **Calls**, **Tokens**, **Failed calls**, **Error rate** or **Average latency**.
2. Hover over the chart to see all five figures for that time slot.

The last 7 days are plotted by hour; 30 and 90 days are plotted by day.

![Read the trend chart (numbers match the steps above)](/manual-shots/en/ai-monitoring/s03.webp)

### Act on problems in System status

**System status** appears next to the chart only when something needs attention. The number in its corner is the count of items. Possible items:

| Item | When it appears | Click to open |
| --- | --- | --- |
| AI usage data failed to load | The usage figures could not be retrieved | **API Key Calls** |
| AI Gateway readiness failed | The gateway is unavailable or not ready to accept requests | **Models** |
| `<n>` model deployments are unhealthy | Some models are offline or degraded (the detail shows how many of each) | **Models** |
| Error rate `<rate>` | The error rate is 5% or higher, or rose by more than 0.5 pp over the previous period (critical from 10%) | **API Key Calls**, filtered to **Failed** |

Click an item to jump to the matching details below.

![Act on problems in System status](/manual-shots/en/ai-monitoring/s04.webp)

### Check model status

1. In **Call details**, open the **Models** tab.
2. Each row shows the **Model**, its **Runtime status** (**Online**, **Degraded**, **Offline** or **Unknown**), **Deployments** (healthy and unhealthy counts), and its **Call Count**, **Total Tokens**, **Failure Rate** and **Avg. Latency** in the period.
3. Click a model name that has calls to open **API Key Calls** filtered to that model.

A model that only appears in past usage and is not known to the gateway shows "—" for runtime status and deployments.

![Check model status (numbers match the steps above)](/manual-shots/en/ai-monitoring/s05.webp)

### Inspect individual calls

1. Open **API Key Calls** for calls that users made with their own keys, or **Template Calls** for the platform's built-in AI features.
2. On **API Key Calls**, choose **All**, **Success** or **Failed**.
3. Type in the search box to filter by user, model, call type or request ID.
4. Read the row: **Time** (with the start of the request ID), **User**, **Model** (shown as "requested → answered" when the upstream answered with a different model), **Type**, **Input** and **Output** tokens, **Duration**, **First token** (time until the first token arrived), **E2E output rate**, **Token evidence** and **Status**.

When **Token evidence** says **Missing upstream**, the model did not report its token usage: tokens are recorded as 0 and no output rate is computed.

![Inspect individual calls (numbers match the steps above)](/manual-shots/en/ai-monitoring/s06.webp)

### Find the heaviest users

1. Open **User Usage**.
2. Each row adds up a user's key calls and built-in AI calls: **Call Count**, **Total Tokens**, **Avg. Latency** and **Failure Rate**.
3. Search by name or email.

![Find the heaviest users (numbers match the steps above)](/manual-shots/en/ai-monitoring/s07.webp)

### Open the Grafana dashboard

Click **View details in Grafana**. The SkyLab AI dashboard opens in a new tab, already signed in.

![Open the Grafana dashboard](/manual-shots/en/ai-monitoring/s08.webp)

## Fields and options

| Item | Meaning | Notes |
| --- | --- | --- |
| Successful calls / Failed calls / Active users | Totals for the selected period | Shown above the detail tabs |
| Detail tabs | **Models**, **API Key Calls**, **Template Calls**, **User Usage** | The number on each tab is its row count |
| Status filter | **All**, **Success**, **Failed** | Only on **API Key Calls** |
| Search | Filters the current tab | Placeholder changes per tab |

## Limits and behaviour to know

- Each call tab loads only the latest 100 calls in the period, and **User Usage** lists the 100 users with the most tokens; search only looks within those rows.
- Changing the detail tab clears the search box.
- This page only collects and shows data; it does not send alerts.

## Related

- [Key Management](/manual/ai-api-keys) — the keys behind the API Key Calls
- [AI API review](/manual/ai-api-review) — approve key requests
- [Resource Monitoring](/manual/monitoring) — platform and machine health
- [Monitoring guide](/guides/monitoring) — the optional monitoring stack and Grafana
- [LiteLLM](/guides/litellm) — the AI Gateway that serves the models
