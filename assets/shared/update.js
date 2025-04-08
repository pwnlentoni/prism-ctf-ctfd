document.getElementById('yaml').addEventListener('change', function(event) {
    if (event.target.files.length > 0) {
        prismUploadFile(event.target.files[0]).then(function(response) {
          document.getElementById('yaml_id').value = response.data[0].id ;
        });
    }
});
function showCurrentYaml() {
    yaml_id_div = document.getElementById('current-yaml-id')
    CTFd.fetch("/api/v1/files/" + yaml_id_div.innerText, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json'
      },
      credentials: "same-origin",
      }).then(function (a) {
        return a.json();
      })
      .then(function (json) {
          console.log(json.data.location)
          yaml_location = json.data.location
          yaml_file_div = document.getElementById('yaml_file')
          yaml_name = yaml_location.split('/')[1]
          yaml_file_div.innerHTML = '<a href="/files/' + yaml_location + '">' + yaml_name + '</a>'
      })
};

document.getElementById("conn-info-refresh").addEventListener("click", (ev) => {
  CTFd.fetch(`/api/v1/plugins/prism_ctf/admin/shared/refresh/${CHALLENGE_ID}`, {method: "POST"}).then(() => window.location.reload())
  ev.preventDefault()
  return false
})

Alpine.start()
showCurrentYaml()