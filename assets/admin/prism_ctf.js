function prismAdmin(apiRoot) {
  return {
    apiRoot,
    instances: [],
    sharedChallenges: [],
    loadingOverview: false,
    overviewFailed: false,
    alert: { message: null, tone: "info" },
    busyActions: {},
    logModal: {
      title: "Logs",
      logs: [],
      open: false,
    },

    init() {
      this.loadOverview();
    },

    emptyAlert() {
      return { message: null, tone: "info" };
    },

    showAlert(message, tone = "info") {
      this.alert = {
        message,
        tone,
      };
    },

    actionKey(kind, action, id) {
      return `${kind}:${action}:${id}`;
    },

    isBusy(key) {
      return this.busyActions[key] === true;
    },

    setBusy(key, busy) {
      this.busyActions = {
        ...this.busyActions,
        [key]: busy,
      };
    },

    ownerLabel(instance) {
      return instance.team_name || instance.user_name || `Owner ${instance.owner_id}`;
    },

    formatDate(value) {
      if (!value) {
        return "—";
      }

      const date = new Date(value);
      if (Number.isNaN(date.getTime())) {
        return value;
      }

      return date.toLocaleString();
    },

    statusBadgeClass(tone) {
      return `badge-${tone}`;
    },

    instanceStatus(instance) {
      if (instance.status_error) {
        return { label: "Status error", tone: "danger" };
      }
      if (!instance.resource_present) {
        return { label: "Missing resource", tone: "secondary" };
      }
      if (instance.status?.ready === true) {
        return { label: "Ready", tone: "success" };
      }
      if (instance.status?.ready === false) {
        return { label: "Pending", tone: "warning" };
      }
      return { label: "Unknown", tone: "secondary" };
    },

    sharedStatus(shared) {
      if (shared.status_error) {
        return { label: "Status error", tone: "danger" };
      }
      if (!shared.resource_present) {
        return { label: "Missing resource", tone: "secondary" };
      }
      if (shared.status?.ready === true) {
        return { label: "Ready", tone: "success" };
      }
      if (shared.status?.ready === false) {
        return { label: "Pending", tone: "warning" };
      }
      return { label: "Unknown", tone: "secondary" };
    },

    connectionKey(entry) {
      return [entry?.name || "", entry?.protocol || "", entry?.hostname || "", entry?.port || ""].join(":");
    },

    connectionLabel(entry) {
      const name = entry?.name ? `${entry.name}: ` : "";
      const host = entry?.hostname ?? "unknown";
      const protocol = entry?.protocol ?? "?";
      const port = entry?.port ? `:${entry.port}` : "";
      return `${name}${protocol} ${host}${port}`;
    },

    logSummary() {
      const count = this.logModal.logs.length;
      return `${count} log stream${count === 1 ? "" : "s"} loaded`;
    },

    closeLogs() {
      this.logModal = {
        ...this.logModal,
        open: false,
      };
    },

    async request(url, options = {}) {
      const headers = new Headers(options.headers || {});
      headers.set("Accept", "application/json");
      if (window.init?.csrfNonce && !headers.has("CSRF-Token")) {
        headers.set("CSRF-Token", window.init.csrfNonce);
      }
      if (options.body !== undefined && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }

      const requestOptions = {
        credentials: "same-origin",
        ...options,
        headers,
      };

      if (window.CTFd && typeof window.CTFd.fetch === "function") {
        return window.CTFd.fetch(url, requestOptions);
      }

      return window.fetch(url, requestOptions);
    },

    async parseResponse(response) {
      const contentType = response.headers.get("content-type") || "";
      let data;

      if (contentType.includes("application/json")) {
        data = await response.json();
      } else {
        const text = await response.text();
        throw new Error(text || `Request failed (${response.status})`);
      }

      if (!response.ok || data.success === false) {
        throw new Error(data.message || `Request failed (${response.status})`);
      }
      return data;
    },

    async loadOverview() {
      this.loadingOverview = true;
      this.overviewFailed = false;

      try {
        const response = await this.request(`${this.apiRoot}/overview`);
        const data = await this.parseResponse(response);
        this.instances = data.instances || [];
        this.sharedChallenges = data.shared_challenges || [];
      } catch (error) {
        this.overviewFailed = true;
        this.instances = [];
        this.sharedChallenges = [];
        this.showAlert(error.message || "Failed to load Prism CTF data", "danger");
      } finally {
        this.loadingOverview = false;
      }
    },

    async openLogs(kind, id) {
      const key = this.actionKey(kind, "logs", id);
      this.setBusy(key, true);

      try {
        const response = await this.request(`${this.apiRoot}/${kind}/${id}/logs`);
        const data = await this.parseResponse(response);
        this.logModal = {
          title: `${kind === "instance" ? "Instance" : "Shared challenge"} #${id} logs`,
          logs: data.logs || [],
          open: true,
        };
      } catch (error) {
        this.showAlert(error.message || "Failed to load logs", "danger");
      } finally {
        this.setBusy(key, false);
      }
    },

    async restart(kind, id) {
      const key = this.actionKey(kind, "restart", id);
      this.setBusy(key, true);

      try {
        const response = await this.request(`${this.apiRoot}/${kind}/${id}/restart`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        const data = await this.parseResponse(response);
        const workloadSummary = (data.workloads || [])
          .map((workload) => `${workload.kind}/${workload.name}`)
          .join(", ");
        this.showAlert(`Restarted ${workloadSummary || "managed workloads"}.`, "success");
        await this.loadOverview();
      } catch (error) {
        this.showAlert(error.message || "Failed to restart workloads", "danger");
      } finally {
        this.setBusy(key, false);
      }
    },
  };
}

Alpine.start();
