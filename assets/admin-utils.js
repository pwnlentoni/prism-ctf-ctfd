function prismUploadFile(file) {
    return new Promise(function (resolve, reject) {
        let formData = new FormData();
        formData.append('file', file);
        formData.append('nonce', CTFd.config.csrfNonce);
        formData.append('type', 'standard')

        $.ajax({
            url: '/api/v1/files',
            type: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            credentials: 'same-origin',
            success: function (response) {
                resolve(response);
            },
            error: function (xhr, status, error) {
                reject(error);
            }
        });
    });
}
