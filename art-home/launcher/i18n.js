/* UI strings. The locale comes from settings.js (your config); add a language by adding a block. */
(function () {
  'use strict';
  var STRINGS = {
    en: {
      days: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
      months: ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'],
      date: function (day, d, month) { return day + ', ' + month + ' ' + d; },
      cameras: 'Cameras', samsung: 'Samsung',
      camHint: 'OK opens live · Back returns', camFullHint: '← → other camera · Back to the grid',
      noSignal: 'no signal', loading: 'loading…', lowQuality: 'reduced quality', noVideo: 'no video right now',
      redOpensLive: '● red opens live', couldNotOpen: 'Could not open ', previewOpen: 'Preview: would open ',
      previewSamsung: 'Preview: would open Samsung Home',
      labels: { person: 'Person', car: 'Car', dog: 'Dog', cat: 'Cat' }
    },
    'pt-BR': {
      days: ['Domingo', 'Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado'],
      months: ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'],
      date: function (day, d, month) { return day + ', ' + d + ' de ' + month; },
      cameras: 'Câmeras', samsung: 'Samsung',
      camHint: 'OK abre ao vivo · Voltar retorna', camFullHint: '← → outra câmera · Voltar ao mosaico',
      noSignal: 'sem sinal', loading: 'carregando…', lowQuality: 'qualidade reduzida', noVideo: 'sem vídeo agora',
      redOpensLive: '● vermelho abre ao vivo', couldNotOpen: 'Não consegui abrir ', previewOpen: 'Prévia: abriria ',
      previewSamsung: 'Prévia: abriria a home da Samsung',
      labels: { person: 'Pessoa', car: 'Carro', dog: 'Cachorro', cat: 'Gato' }
    }
  };
  var locale = (window.ART_HOME_SETTINGS && window.ART_HOME_SETTINGS.locale) || 'en';
  window.T = STRINGS[locale] || STRINGS.en;
})();
