/* Boton de mostrar/ocultar contraseña del login (antes inline en login.html). */
  document.getElementById("login-eye-btn").addEventListener("click", function() {
    var inp = document.getElementById("password");
    var show = inp.type === "password";
    inp.type = show ? "text" : "password";
    this.innerHTML = show ? "&#128584;" : "&#128065;";
  });
