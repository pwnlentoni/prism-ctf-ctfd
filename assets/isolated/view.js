CTFd._internal.challenge.data = undefined;

// TODO: Remove in CTFd v4.0
CTFd._internal.challenge.renderer = null;

CTFd._internal.challenge.preRender = function () {
  Alpine.store('prism_instance', {
    source: null,
    exists: false,
    detail: null,
    error: null,

    async create() {
      const resp = await CTFd.fetch(`/api/v1/plugins/prism_ctf/instance/${CTFd._internal.challenge.data.id}`, {
        method: "PUT",
        body: JSON.stringify({}),
      })
      const data = await resp.json();
      if (!data.success && data.message != "instance already exists") {
        this.error = data.message ?? "An unknown error occured, please open a ticket"
      } else {
        this.check()
      }
    },
    async delete() {
      const resp = await CTFd.fetch(`/api/v1/plugins/prism_ctf/instance/${CTFd._internal.challenge.data.id}`, {
        method: "DELETE",
        body: JSON.stringify({}),
      })
      const data = await resp.json();
      if (!data.success) {
        this.error = data.message ?? "An unknown error occured, please open a ticket"
      }
    },
    async extend() {
      const resp = await CTFd.fetch(`/api/v1/plugins/prism_ctf/instance/${CTFd._internal.challenge.data.id}/extend`, {
        method: "POST",
        body: JSON.stringify({}),
      })
      const data = await resp.json();
      if (!data.success) {
        this.error = data.message ?? "An unknown error occured, please open a ticket"
      }
    },
    check() {
      const source = new EventSource(`/api/v1/plugins/prism_ctf/instance/${CTFd._internal.challenge.data.id}`)
      this.source = source

      source.addEventListener("update", (ev) => {
        this.detail = JSON.parse(ev.data)
        this.exists = true
        console.log("got update", this.detail)
      })
      source.addEventListener("delete", () => {
        this.stop_sse()
        this.exists = false
        console.log("instance deleted")
        this.error = "instance deleted"
      })
      source.addEventListener("error", (ev) => {
        console.log("sse error", ev)
        this.stop_sse()
      })
    },
    stop_sse() {
      this.source.close()
      this.source = null
      this.detail = null
    }
  })
  Alpine.store('prism_instance').check()
};

// TODO: Remove in CTFd v4.0
CTFd._internal.challenge.render = null;

CTFd._internal.challenge.postRender = function () { };

CTFd._internal.challenge.submit = function (preview) {
  var challenge_id = parseInt(CTFd.lib.$("#challenge-id").val());
  var submission = CTFd.lib.$("#challenge-input").val();

  var body = {
    challenge_id: challenge_id,
    submission: submission
  };
  var params = {};
  if (preview) {
    params["preview"] = true;
  }

  return CTFd.api.post_challenge_attempt(params, body).then(function (response) {
    if (response.status === 429) {
      // User was ratelimited but process response
      return response;
    }
    if (response.status === 403) {
      // User is not logged in or CTF is paused.
      return response;
    }
    return response;
  });
};
