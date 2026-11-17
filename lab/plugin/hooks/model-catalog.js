export default {
  "schema": 1,
  "revision": 45,
  "catalogVersion": "cli-2.1.326",
  "discovery": "Public --help advertised aliases only; not an exhaustive model/entitlement list. Stable aliases follow installed Claude Code defaults.",
  "models": {
    "sonnet": {
      "id": "claude-sonnet-5-5",
      "alias": "sonnet",
      "effort": "medium",
      "advisor": "opus",
      "rates": [
        2,
        10,
        0.1,
        4
      ],
      "lastObservedAt": 1794956061158
    },
    "opus": {
      "id": "claude-opus-5-5",
      "alias": "opus",
      "effort": "high",
      "advisor": "opus",
      "rates": [
        4,
        20,
        0.2,
        8
      ],
      "lastObservedAt": 1794615012157
    },
    "fable": {
      "id": "claude-fable-5-2",
      "alias": "fable",
      "effort": "xhigh",
      "advisor": "fable",
      "rates": null,
      "availabilityGate": true,
      "lastObservedAt": 1794615009673
    }
  },
  "claudeVersion": "2.1.326"
};
