// app.js — Логіка Telegram WebApp «Німецька для українців»

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  document.body.classList.add('telegram-theme');
}

// Початкові резервні дані на випадок локального запуску без сервера
const DEFAULT_WORDS = [
  {
    word: "die Anmeldung",
    article: "die",
    transcription: "[ді Анме́льдунґ]",
    translation: "реєстрація місця проживання (прописка)",
    level: "A1-A2",
    rubric: "Бюрократія",
    example_de: "Ich brauche einen Termin für die Anmeldung.",
    example_ua: "Мені потрібен запис на реєстрацію місця проживання."
  },
  {
    word: "der Feierabend",
    article: "der",
    transcription: "[дер Фа́єрабент]",
    translation: "кінець робочого дня / вечірній відпочинок",
    level: "A1-A2",
    rubric: "Граматика",
    example_de: "Schönen Feierabend allerseits!",
    example_ua: "Гарного відпочинку після роботи всім!"
  },
  {
    word: "das Pfand",
    article: "das",
    transcription: "[дас Пфанд]",
    translation: "застава за тару або пляшки",
    level: "A1-A2",
    rubric: "Побут",
    example_de: "Vergiss nicht, das Pfand im Supermarkt abzugeben!",
    example_ua: "Не забудь здати пляшки під заставу в супермаркеті!"
  },
  {
    word: "Passt schon!",
    article: "",
    transcription: "[па́ст шон!]",
    translation: "Все гаразд! / Решти не треба!",
    level: "Розмовна",
    rubric: "Розмовна фраза",
    example_de: "Hier sind zehn Euro. Passt schon!",
    example_ua: "Ось десять євро. Решти не треба!"
  },
  {
    word: "die Mülltrennung",
    article: "die",
    transcription: "[ді Мю́льтреннунґ]",
    translation: "сортування сміття",
    level: "A1-A2",
    rubric: "Побут",
    example_de: "In Deutschland ist Mülltrennung Pflicht.",
    example_ua: "У Німеччині сортування сміття є обов'язковим."
  },
  {
    word: "die Gemütlichkeit",
    article: "die",
    transcription: "[ді Ґемю́тліхькайт]",
    translation: "затишок, душевний комфорт та спокій",
    level: "B1-B2",
    rubric: "Ідіоми",
    example_de: "Hier herrscht echte Gemütlichkeit.",
    example_ua: "Тут панує справжній затишок."
  }
];

let wordsDatabase = [];
let currentCardIndex = 0;
let quizScore = 0;
let currentQuizItem = null;

// Тактильний відгук у Telegram
function triggerHaptic(type = 'light') {
  if (tg?.HapticFeedback) {
    tg.HapticFeedback.impactOccurred(type);
  }
}

// Синтез мови (німецька вимова)
function speakGerman(text) {
  triggerHaptic('light');
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'de-DE';
    utterance.rate = 0.9;
    window.speechSynthesis.speak(utterance);
  }
}

// Завантаження даних із history.json
async function loadWords() {
  try {
    const res = await fetch('history.json?t=' + Date.now());
    if (res.ok) {
      const data = await res.json();
      const parsedWords = [];

      // 1. Обробка списку history
      if (Array.isArray(data.history)) {
        data.history.forEach(item => {
          if (item.type === 'afternoon_phrase' || item.phrase) {
            parsedWords.push({
              word: item.phrase || item.word,
              article: '',
              transcription: '',
              translation: item.translation || '',
              level: 'Розмовна',
              rubric: 'Розмовна фраза',
              example_de: item.phrase || '',
              example_ua: item.translation || ''
            });
          } else if (item.word) {
            let art = '';
            const lowerWord = item.word.toLowerCase();
            if (lowerWord.startsWith('der ')) art = 'der';
            else if (lowerWord.startsWith('die ')) art = 'die';
            else if (lowerWord.startsWith('das ')) art = 'das';

            parsedWords.push({
              word: item.word,
              article: art,
              transcription: '',
              translation: item.translation || '',
              level: item.level || 'A1-B2',
              rubric: item.rubric || 'Слово дня',
              example_de: item.word,
              example_ua: item.translation || ''
            });
          }
        });
      }

      if (parsedWords.length > 0) {
        // Об'єднуємо з дефолтними для повноти
        const map = new Map();
        [...DEFAULT_WORDS, ...parsedWords].forEach(item => map.set(item.word, item));
        wordsDatabase = Array.from(map.values());
      } else {
        wordsDatabase = DEFAULT_WORDS;
      }
    } else {
      wordsDatabase = DEFAULT_WORDS;
    }
  } catch (e) {
    console.warn('Не вдалося завантажити history.json, використовуються стандартні слова:', e);
    wordsDatabase = DEFAULT_WORDS;
  }

  document.getElementById('wordsCount').textContent = wordsDatabase.length;
  renderFlashcard(currentCardIndex);
  renderDictionary(wordsDatabase);
  initNextQuiz();
}

// Рендер поточної флеш-картки
function renderFlashcard(index) {
  if (!wordsDatabase.length) return;
  const item = wordsDatabase[index];
  const card = document.getElementById('flashcard');
  card.classList.remove('flipped');

  document.getElementById('cardRubric').textContent = item.rubric || 'СЛОВО ДНЯ';
  document.getElementById('cardLevel').textContent = item.level || 'A2';
  document.getElementById('cardWord').textContent = item.word;
  document.getElementById('cardTranscription').textContent = item.transcription || '';
  document.getElementById('cardTranslation').textContent = item.translation;
  document.getElementById('cardExampleDe').textContent = item.example_de ? `»${item.example_de}«` : item.word;
  document.getElementById('cardExampleUa').textContent = item.example_ua ? `— ${item.example_ua}` : item.translation;
  document.getElementById('cardCounter').textContent = `Картка ${index + 1} з ${wordsDatabase.length}`;
}

// Ініціалізація перемикання карток
function setupFlashcards() {
  const card = document.getElementById('flashcard');
  const flipBtn = document.getElementById('flipCardBtn');
  const prevBtn = document.getElementById('prevCardBtn');
  const nextBtn = document.getElementById('nextCardBtn');
  const audioBtn = document.getElementById('playAudioBtn');

  const toggleFlip = () => {
    card.classList.toggle('flipped');
    triggerHaptic('medium');
  };

  card.addEventListener('click', (e) => {
    if (e.target.closest('#playAudioBtn')) return;
    toggleFlip();
  });

  flipBtn.addEventListener('click', toggleFlip);

  prevBtn.addEventListener('click', () => {
    if (currentCardIndex > 0) {
      currentCardIndex--;
      renderFlashcard(currentCardIndex);
      triggerHaptic('light');
    }
  });

  nextBtn.addEventListener('click', () => {
    if (currentCardIndex < wordsDatabase.length - 1) {
      currentCardIndex++;
    } else {
      currentCardIndex = 0;
    }
    renderFlashcard(currentCardIndex);
    triggerHaptic('light');
  });

  audioBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const item = wordsDatabase[currentCardIndex];
    speakGerman(item.example_de || item.word);
  });
}

// Рендер списку словника
function renderDictionary(items) {
  const list = document.getElementById('dictionaryList');
  list.innerHTML = '';

  if (items.length === 0) {
    list.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 30px;">Нічого не знайдено 🔍</div>';
    return;
  }

  items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'dict-item';
    div.innerHTML = `
      <div class="dict-left">
        <div class="dict-word">${item.word}</div>
        <div class="dict-translation">${item.translation}</div>
      </div>
      <div class="dict-right">
        <button class="speak-icon-btn" title="Вимова">🔊</button>
      </div>
    `;

    div.querySelector('.speak-icon-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      speakGerman(item.example_de || item.word);
    });

    list.appendChild(div);
  });
}

// Пошук та фільтри
function setupSearchAndFilters() {
  const searchInput = document.getElementById('searchInput');
  const clearBtn = document.getElementById('clearSearchBtn');
  const filterPills = document.querySelectorAll('.filter-pill');
  let currentFilter = 'all';

  function applyFilters() {
    const query = searchInput.value.toLowerCase().trim();
    clearBtn.style.display = query ? 'block' : 'none';

    let filtered = wordsDatabase.filter(item => {
      const matchText = (item.word && item.word.toLowerCase().includes(query)) ||
                        (item.translation && item.translation.toLowerCase().includes(query));
      if (!matchText) return false;

      if (currentFilter === 'all') return true;
      if (currentFilter === 'phrase') return item.article === '' || item.rubric.includes('фраза');
      return item.article === currentFilter;
    });

    renderDictionary(filtered);
  }

  searchInput.addEventListener('input', applyFilters);

  clearBtn.addEventListener('click', () => {
    searchInput.value = '';
    applyFilters();
    searchInput.focus();
  });

  filterPills.forEach(pill => {
    pill.addEventListener('click', () => {
      filterPills.forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      currentFilter = pill.getAttribute('data-filter');
      triggerHaptic('light');
      applyFilters();
    });
  });
}

// Тренажер знань (Quiz)
function initNextQuiz() {
  if (wordsDatabase.length < 2) return;
  const feedback = document.getElementById('quizFeedback');
  const nextBtn = document.getElementById('nextQuizBtn');
  feedback.style.display = 'none';
  nextBtn.style.display = 'none';

  // Обираємо випадкове цільове слово
  const target = wordsDatabase[Math.floor(Math.random() * wordsDatabase.length)];
  currentQuizItem = target;
  document.getElementById('quizWord').textContent = target.word;

  // Формуємо 4 варіанти відповіді (1 правильний, 3 дистрактори)
  const options = [target.translation];
  const others = wordsDatabase.filter(w => w.translation !== target.translation);
  shuffleArray(others);
  for (let i = 0; i < Math.min(3, others.length); i++) {
    options.push(others[i].translation);
  }
  shuffleArray(options);

  const container = document.getElementById('quizOptions');
  container.innerHTML = '';

  options.forEach(opt => {
    const btn = document.createElement('button');
    btn.className = 'quiz-opt-btn';
    btn.textContent = opt;

    btn.addEventListener('click', () => {
      // Блокуємо всі кнопки після кліку
      const allBtns = container.querySelectorAll('.quiz-opt-btn');
      allBtns.forEach(b => b.disabled = true);

      if (opt === target.translation) {
        btn.classList.add('correct');
        quizScore += 10;
        feedback.className = 'quiz-feedback correct';
        feedback.textContent = '🎉 Чудово! Правильна відповідь!';
        triggerHaptic('success');
      } else {
        btn.classList.add('wrong');
        feedback.className = 'quiz-feedback wrong';
        feedback.textContent = `❌ Неправильно. Правильна відповідь: ${target.translation}`;
        triggerHaptic('error');
        allBtns.forEach(b => {
          if (b.textContent === target.translation) b.classList.add('correct');
        });
      }

      document.getElementById('quizScore').textContent = `Рахунок: ${quizScore}`;
      feedback.style.display = 'block';
      nextBtn.style.display = 'block';
    });

    container.appendChild(btn);
  });
}

function shuffleArray(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
}

// Навігація та перемикання вкладок
function setupTabs() {
  const tabs = document.querySelectorAll('.tab-btn');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

      tab.classList.add('active');
      const targetId = tab.getAttribute('data-tab');
      document.getElementById(targetId).classList.add('active');
      triggerHaptic('light');
    });
  });

  document.getElementById('nextQuizBtn').addEventListener('click', () => {
    initNextQuiz();
    triggerHaptic('light');
  });
}

// Запуск застосунку
document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupFlashcards();
  setupSearchAndFilters();
  loadWords();
});
