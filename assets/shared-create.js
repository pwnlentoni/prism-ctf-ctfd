document.getElementById('yaml').addEventListener('change', function(event) {
    if (event.target.files.length > 0) {
        prismUploadFile(event.target.files[0]).then(function(response) {
          document.getElementById('yaml_id').value = response.data[0].id ;
        });
    }
});