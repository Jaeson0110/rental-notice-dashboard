const CONFIG = window.APP_CONFIG || {};
const STAGES = ["관심 없음", "검토 필요", "신청 예정", "서류 준비 중", "신청 완료", "제외"];
const LOCAL_KEY = "rental-notice-dashboard-state-v1";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

let notices = [];
let states = new Map();
let activeStage = "all";
let supabase = null;
let currentUser = null;
let toastTimer = null;

const els = {
  loginGate: $("#loginGate"), loginForm: $("#loginForm"), loginEmail: $("#loginEmail"), loginPassword: $("#loginPassword"), loginMessage: $("#loginMessage"),
  logoutButton: $("#logoutButton"), modeBadge: $("#modeBadge"), refreshButton: $("#refreshButton"),
  updatedAt: $("#updatedAt"), dataHealth: $("#dataHealth"), noticeList: $("#noticeList"), emptyState: $("#emptyState"),
  searchInput: $("#searchInput"), agencyFilter: $("#agencyFilter"), regionFilter: $("#regionFilter"), typeFilter: $("#typeFilter"),
  statusFilter: $("#statusFilter"), sortSelect: $("#sortSelect"), resetFilters: $("#resetFilters"), stageTabs: $("#stageTabs"),
  detailDialog: $("#detailDialog"), detailContent: $("#detailContent"), toast: $("#toast")
};

function configureStatusFilter() {
  els.statusFilter.innerHTML = `
    <option value="current">진행 중만</option>
    <option value="all">전체 상태 · 마감 포함</option>
    <option value="접수중">접수 중</option>
    <option value="접수예정">접수 예정</option>
    <option value="공고중">공고 중</option>
    <option value="일정 확인 필요">일정 확인 필요</option>
    <option value="후속공고">후속 공고</option>
    <option value="마감">마감</option>
  `;
  els.statusFilter.value = "current";
}

function ensureExcludedTab() {
  if ($("#countExcluded")) return;

  const button = document.createElement("button");
  button.type = "button";
  button.dataset.stage = "제외";
  button.innerHTML = '제외 <span id="countExcluded">0</span>';
  els.stageTabs.appendChild(button);
}

function isSupabaseConfigured() {
  return Boolean(CONFIG.supabaseUrl && CONFIG.supabaseAnonKey);
}

async function init() {
  configureStatusFilter();
  ensureExcludedTab();
  wireEvents();
  await setupDataMode();
  await loadNotices();
  populateFilters();
  render();
}

async function setupDataMode() {
  if (!isSupabaseConfigured()) {
    els.modeBadge.textContent = "데모 모드 · 이 기기에 저장";
    loadLocalStates();
    return;
  }

  const { createClient } = await import("https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm");
  supabase = createClient(CONFIG.supabaseUrl, CONFIG.supabaseAnonKey);
  const { data } = await supabase.auth.getSession();
  if (data.session) await acceptSession(data.session);
  else showLoginGate();

  supabase.auth.onAuthStateChange(async (_event, session) => {
    if (session) await acceptSession(session);
    else showLoginGate();
  });
}

async function acceptSession(session) {
  currentUser = session.user;
  const email = currentUser.email;
  const { data, error } = await supabase.from("allowed_users").select("email, display_name").eq("email", email).maybeSingle();
  if (error || !data) {
    await supabase.auth.signOut();
    showLoginGate("이 이메일은 허용 목록에 없습니다.");
    return;
  }
  els.loginGate.classList.add("hidden");
  els.loginGate.setAttribute("aria-hidden", "true");
  els.modeBadge.textContent = `${data.display_name || email} · 공동 모드`;
  els.logoutButton.classList.remove("hidden");
  await loadRemoteStates();
  subscribeRemoteStates();
  render();
}

function showLoginGate(message = "") {
  currentUser = null;
  els.loginGate.classList.remove("hidden");
  els.loginGate.setAttribute("aria-hidden", "false");
  els.logoutButton.classList.add("hidden");
  els.loginMessage.textContent = message;
}

async function loadNotices() {
  try {
    const response = await fetch(`./data/notices.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    notices = Array.isArray(payload) ? payload : payload.notices || [];
    const updated = Array.isArray(payload) ? null : payload.updatedAt;
    els.updatedAt.textContent = updated ? formatDateTime(updated) : "업데이트 시간 없음";
    els.dataHealth.textContent = `${notices.length}개 공고를 정상적으로 불러왔습니다.`;
  } catch (error) {
    notices = [];
    els.updatedAt.textContent = "불러오기 실패";
    els.dataHealth.textContent = "data/notices.json을 확인해 주세요.";
    showToast("공고 데이터를 불러오지 못했습니다.");
  }
}

function loadLocalStates() {
  try {
    const raw = JSON.parse(localStorage.getItem(LOCAL_KEY) || "{}");
    states = new Map(Object.entries(raw));
  } catch {
    states = new Map();
  }
}

function saveLocalStates() {
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(Object.fromEntries(states)));
  } catch {
    showToast("이 브라우저에서는 상태를 저장할 수 없습니다.");
  }
}

async function loadRemoteStates() {
  const { data, error } = await supabase.from("notice_states").select("*");
  if (error) {
    showToast("공동 상태를 불러오지 못했습니다.");
    return;
  }
  states = new Map((data || []).map(row => [row.notice_id, {
    favorite: row.favorite,
    stage: row.stage,
    memo: row.memo || "",
    updatedAt: row.updated_at,
    updatedBy: row.updated_by
  }]));
}

function subscribeRemoteStates() {
  supabase.channel("notice-state-sync")
    .on("postgres_changes", { event: "*", schema: "public", table: "notice_states" }, async () => {
      await loadRemoteStates();
      render();
    }).subscribe();
}

function defaultState() {
  return { favorite: false, stage: "관심 없음", memo: "", updatedAt: null, updatedBy: null };
}

function getState(id) {
  return { ...defaultState(), ...(states.get(id) || {}) };
}

async function patchState(id, patch) {
  const next = { ...getState(id), ...patch, updatedAt: new Date().toISOString(), updatedBy: currentUser?.email || "local" };
  states.set(id, next);
  render();

  if (!supabase) {
    saveLocalStates();
    return;
  }

  const { error } = await supabase.from("notice_states").upsert({
    notice_id: id,
    favorite: next.favorite,
    stage: next.stage,
    memo: next.memo,
    updated_by: currentUser.email,
    updated_at: next.updatedAt
  }, { onConflict: "notice_id" });
  if (error) showToast("저장하지 못했습니다. 다시 시도해 주세요.");
}

function wireEvents() {
  [els.searchInput, els.agencyFilter, els.regionFilter, els.typeFilter, els.statusFilter, els.sortSelect]
    .forEach(el => el.addEventListener("input", render));

  els.resetFilters.addEventListener("click", () => {
    els.searchInput.value = "";
    els.agencyFilter.value = "all";
    els.regionFilter.value = "all";
    els.typeFilter.value = "all";
    els.statusFilter.value = "current";
    els.sortSelect.value = "newest";
    activeStage = "all";
    updateActiveTab();
    render();
  });

  els.stageTabs.addEventListener("click", event => {
    const button = event.target.closest("button[data-stage]");
    if (!button) return;
    activeStage = button.dataset.stage;
    // 관심·진행 상태 탭에서는 마감된 신청 공고도 계속 보여 줍니다.
    // 다시 전체 탭으로 돌아오면 기본값인 진행 중 공고만 표시합니다.
    els.statusFilter.value = activeStage === "all" ? "current" : "all";
    updateActiveTab();
    render();
  });

  els.noticeList.addEventListener("click", async event => {
    const card = event.target.closest(".notice-card");
    if (!card) return;
    const id = card.dataset.id;
    if (event.target.closest(".favorite-button")) {
      const state = getState(id);
      const favorite = !state.favorite;
      const stage = favorite && ["관심 없음", "제외"].includes(state.stage)
        ? "검토 필요"
        : state.stage;
      await patchState(id, { favorite, stage });
    }
    if (event.target.closest(".detail-button") || event.target.closest(".notice-title-button")) openDetail(id);
  });

  els.noticeList.addEventListener("change", async event => {
    if (!event.target.matches(".stage-select")) return;

    const card = event.target.closest(".notice-card");
    const stage = event.target.value;
    const previous = getState(card.dataset.id);

    let favorite = previous.favorite;
    if (stage === "제외") favorite = false;
    else if (stage !== "관심 없음") favorite = true;

    await patchState(card.dataset.id, { stage, favorite });

    if (stage === "제외" && activeStage !== "제외") {
      showToast("제외함으로 이동했습니다.");
    }
  });

  els.detailContent.addEventListener("click", async event => {
    const save = event.target.closest("[data-save-memo]");
    if (!save) return;
    const id = save.dataset.saveMemo;
    const memo = $("#memoInput").value.trim();
    await patchState(id, { memo });
    showToast("메모를 저장했습니다.");
    els.detailDialog.close();
  });

  els.refreshButton.addEventListener("click", async () => {
    els.refreshButton.disabled = true;
    els.refreshButton.textContent = "…";
    await loadNotices();
    if (supabase && currentUser) await loadRemoteStates();
    populateFilters();
    render();
    els.refreshButton.disabled = false;
    els.refreshButton.textContent = "↻";
  });

  els.loginForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!supabase) return;
    els.loginMessage.textContent = "로그인 중입니다.";
    const { error } = await supabase.auth.signInWithPassword({
      email: els.loginEmail.value.trim(),
      password: els.loginPassword.value
    });
    els.loginMessage.textContent = error ? `로그인 실패: ${error.message}` : "";
  });

  els.logoutButton.addEventListener("click", () => supabase?.auth.signOut());
}

function populateFilters() {
  const regions = [...new Set(notices.flatMap(n => n.regions || []).filter(Boolean))].sort((a,b) => a.localeCompare(b, "ko"));
  const types = [...new Set(notices.map(n => n.noticeType).filter(Boolean))].sort((a,b) => a.localeCompare(b, "ko"));
  els.regionFilter.innerHTML = '<option value="all">전체 지역</option>' + regions.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  els.typeFilter.innerHTML = '<option value="all">전체 유형</option>' + types.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
}

function filteredNotices() {
  const query = normalizeText(els.searchInput.value);
  const agency = els.agencyFilter.value;
  const region = els.regionFilter.value;
  const type = els.typeFilter.value;
  const status = els.statusFilter.value;

  const result = notices.filter(n => {
    const state = getState(n.id);
    const haystack = normalizeText([n.title, n.noticeType, ...(n.regions || []), ...(n.targetGroups || [])].join(" "));
    if (query && !haystack.includes(query)) return false;
    if (agency !== "all" && n.agency !== agency) return false;
    if (region !== "all" && !(n.regions || []).includes(region)) return false;
    if (type !== "all" && n.noticeType !== type) return false;
    const noticeStatus = displayStatus(n);
    if (status === "current" && ["마감", "후속공고"].includes(noticeStatus)) return false;
    if (!["all", "current"].includes(status) && noticeStatus !== status) return false;
    if (activeStage === "제외") {
      if (state.stage !== "제외") return false;
    } else {
      // 제외한 공고는 전체·관심·진행 상태 목록에서 모두 숨깁니다.
      if (state.stage === "제외") return false;
      if (activeStage === "favorite" && !state.favorite) return false;
      if (!["all", "favorite"].includes(activeStage) && state.stage !== activeStage) return false;
    }
    return true;
  });

  const sort = els.sortSelect.value;
  result.sort((a, b) => {
    if (sort === "deadline") return dateValue(a.applyEnd, 9999999999999) - dateValue(b.applyEnd, 9999999999999);
    if (sort === "updated") return dateValue(getState(b.id).updatedAt, 0) - dateValue(getState(a.id).updatedAt, 0);
    return dateValue(b.publishedAt, 0) - dateValue(a.publishedAt, 0);
  });
  return result;
}

function render() {
  renderStats();
  const list = filteredNotices();
  $("#resultCount").textContent = list.length;
  els.noticeList.innerHTML = list.map(renderCard).join("");
  els.emptyState.classList.toggle("hidden", list.length > 0);
  updateCounts();
}

function renderStats() {
  const today = new Date();
  today.setHours(0,0,0,0);
  const availableNotices = notices.filter(n => getState(n.id).stage !== "제외");

  // 수집기가 오늘 처음 발견했다는 이유만으로 오래된 공고를 NEW로 표시하지 않습니다.
  const newCount = availableNotices.filter(n => isPublishedToday(n.publishedAt) && !["마감", "후속공고"].includes(displayStatus(n))).length;
  const openCount = availableNotices.filter(n => ["접수중", "공고중"].includes(displayStatus(n))).length;
  const closingCount = availableNotices.filter(n => { const d = daysUntil(n.applyEnd); return d >= 0 && d <= 7; }).length;
  const favoriteCount = availableNotices.filter(n => getState(n.id).favorite).length;
  $("#statNew").textContent = newCount;
  $("#statOpen").textContent = openCount;
  $("#statClosing").textContent = closingCount;
  $("#statFavorite").textContent = favoriteCount;
}

function updateCounts() {
  const stageCounts = {
    all: 0,
    favorite: 0,
    "검토 필요": 0,
    "신청 예정": 0,
    "서류 준비 중": 0,
    "신청 완료": 0,
    "제외": 0
  };

  notices.forEach(n => {
    const state = getState(n.id);

    if (state.stage === "제외") {
      stageCounts["제외"]++;
      return;
    }

    if (!["마감", "후속공고"].includes(displayStatus(n))) stageCounts.all++;
    if (state.favorite) stageCounts.favorite++;
    if (stageCounts[state.stage] !== undefined) stageCounts[state.stage]++;
  });

  $("#countAll").textContent = stageCounts.all;
  $("#countFavorite").textContent = stageCounts.favorite;
  $("#countReview").textContent = stageCounts["검토 필요"];
  $("#countPlanned").textContent = stageCounts["신청 예정"];
  $("#countDocs").textContent = stageCounts["서류 준비 중"];
  $("#countDone").textContent = stageCounts["신청 완료"];
  $("#countExcluded").textContent = stageCounts["제외"];
}

function updateActiveTab() {
  $$("#stageTabs button").forEach(btn => btn.classList.toggle("active", btn.dataset.stage === activeStage));
}

function renderCard(n) {
  const state = getState(n.id);
  const deadline = deadlineLabel(n.applyEnd);
  const targetText = (n.targetGroups || []).length ? (n.targetGroups || []).join(" · ") : "대상은 공식 공고문 확인";
  const regionText = (n.regions || []).join(" · ") || "지역 미분류";
  return `
    <article class="notice-card" data-id="${escapeHtml(n.id)}">
      <div class="notice-main">
        <div class="notice-tags">
          ${isRecentNotice(n.publishedAt) ? '<span class="tag new">NEW</span>' : ''}
          <span class="tag agency-${escapeHtml(n.agency)}">${escapeHtml(n.agency)}</span>
          <span class="tag type">${escapeHtml(n.noticeType || "임대주택")}</span>
        </div>
        <button class="notice-title-button" type="button">${escapeHtml(n.title)}</button>
        <div class="notice-meta">
          <span>⌖ ${escapeHtml(regionText)}</span>
          <span>공고 ${formatDate(n.publishedAt)}</span>
          <span>마감 ${formatDate(n.applyEnd)}</span>
          <span>${escapeHtml(displayStatus(n))}</span>
        </div>
        <p class="notice-targets">대상: ${escapeHtml(targetText)}</p>
        ${state.memo ? `<div class="notice-note">${escapeHtml(state.memo)}</div>` : ""}
      </div>
      <div class="notice-side">
        <span class="deadline ${deadline.urgent ? "urgent" : ""}">${deadline.text}</span>
        <select class="stage-select" aria-label="진행 상태">
          ${STAGES.map(stage => `<option ${state.stage === stage ? "selected" : ""}>${stage}</option>`).join("")}
        </select>
        <div class="card-actions">
          <button class="favorite-button ${state.favorite ? "active" : ""}" type="button" title="관심 공고">${state.favorite ? "♥" : "♡"}</button>
          <button class="detail-button" type="button">상세·메모</button>
        </div>
      </div>
    </article>`;
}

function openDetail(id) {
  const n = notices.find(item => item.id === id);
  if (!n) return;
  const state = getState(id);
  els.detailContent.innerHTML = `
    <div class="detail-head">
      <div class="notice-tags"><span class="tag agency-${escapeHtml(n.agency)}">${escapeHtml(n.agency)}</span><span class="tag type">${escapeHtml(n.noticeType)}</span></div>
      <h2>${escapeHtml(n.title)}</h2>
    </div>
    <div class="detail-grid">
      <div class="detail-item"><span>지역</span><strong>${escapeHtml((n.regions || []).join(" · ") || "미분류")}</strong></div>
      <div class="detail-item"><span>대상</span><strong>${escapeHtml((n.targetGroups || []).join(" · ") || "공고문 확인")}</strong></div>
      <div class="detail-item"><span>공고일</span><strong>${formatDate(n.publishedAt)}</strong></div>
      <div class="detail-item"><span>신청기간</span><strong>${formatPeriod(n.applyStart, n.applyEnd)} · ${deadlineLabel(n.applyEnd).text}</strong></div>
      ${n.documentStart || n.documentEnd ? `<div class="detail-item"><span>서류제출기간</span><strong>${formatPeriod(n.documentStart, n.documentEnd)}</strong></div>` : ""}
      ${n.winnerAt ? `<div class="detail-item"><span>당첨자 발표</span><strong>${formatDate(n.winnerAt)}</strong></div>` : ""}
      ${n.contractStart || n.contractEnd ? `<div class="detail-item"><span>계약기간</span><strong>${formatPeriod(n.contractStart, n.contractEnd)}</strong></div>` : ""}
      <div class="detail-item"><span>공고 상태</span><strong>${escapeHtml(displayStatus(n))}</strong></div>
      <div class="detail-item"><span>일정 출처</span><strong>${escapeHtml(n.scheduleSource || "공식 목록")} · ${escapeHtml(n.scheduleConfidence || "확인필요")}</strong></div>
      <div class="detail-item"><span>우리 진행 상태</span><strong>${escapeHtml(state.stage)}</strong></div>
    </div>
    <section class="detail-section">
      <h3>공동 메모</h3>
      <textarea id="memoInput" placeholder="확인할 자격, 보증금, 서류 등을 적어 두세요.">${escapeHtml(state.memo)}</textarea>
    </section>
    <div class="dialog-actions">
      <a href="${escapeAttribute(n.officialUrl || "#")}" target="_blank" rel="noopener noreferrer">공식 공고 열기</a>
      <button type="button" data-save-memo="${escapeHtml(id)}">메모 저장</button>
    </div>`;
  els.detailDialog.showModal();
}

function displayStatus(n) {
  const raw = String(n.status || "").replace(/\s/g, "");
  if (raw.includes("후속공고")) return "후속공고";
  if (raw.includes("일정확인필요")) return "일정 확인 필요";

  const now = Date.now();
  if (n.applyEnd && dateValue(n.applyEnd, 0) < now) return "마감";
  if (n.applyStart && dateValue(n.applyStart, 0) > now) return "접수예정";
  if (n.applyStart && n.applyEnd && dateValue(n.applyStart, 0) <= now && dateValue(n.applyEnd, 0) >= now) return "접수중";

  if (raw.includes("마감")) return "마감";
  if (raw.includes("접수중")) return "접수중";
  if (raw.includes("접수예정")) return "접수예정";
  return raw || (n.applyEnd ? "공고중" : "일정 확인 필요");
}

function deadlineLabel(date) {
  if (!date) return { text: "마감일 확인 필요", urgent: false };
  const days = daysUntil(date);
  if (days < 0) return { text: "접수 마감", urgent: false };
  if (days === 0) return { text: "오늘 마감", urgent: true };
  return { text: `D-${days}`, urgent: days <= 7 };
}

function daysUntil(date) {
  if (!date) return 9999;
  const target = new Date(`${String(date).slice(0,10)}T23:59:59`);
  return Math.ceil((target.getTime() - Date.now()) / 86400000);
}

function isPublishedToday(value) {
  if (!value) return false;
  const published = new Date(value);
  if (Number.isNaN(published.getTime())) return false;
  const today = new Date();
  return published.getFullYear() === today.getFullYear()
    && published.getMonth() === today.getMonth()
    && published.getDate() === today.getDate();
}

function isRecentNotice(value, days = 7) {
  if (!value) return false;
  const published = new Date(value);
  if (Number.isNaN(published.getTime())) return false;
  const today = new Date();
  today.setHours(23, 59, 59, 999);
  const age = today.getTime() - published.getTime();
  return age >= 0 && age <= days * 86400000;
}

function sameDate(value, date) {
  if (!value) return false;
  const d = new Date(value);
  return d.getFullYear() === date.getFullYear() && d.getMonth() === date.getMonth() && d.getDate() === date.getDate();
}
function dateValue(value, fallback) { const n = value ? new Date(value).getTime() : NaN; return Number.isNaN(n) ? fallback : n; }
function formatDate(value) { return value ? new Intl.DateTimeFormat("ko-KR", { year:"numeric", month:"2-digit", day:"2-digit" }).format(new Date(value)) : "확인 필요"; }
function formatPeriod(start, end) {
  if (!start && !end) return "확인 필요";
  if (start && end && String(start).slice(0, 10) === String(end).slice(0, 10)) return formatDate(start);
  return `${formatDate(start)} ~ ${formatDate(end)}`;
}
function formatDateTime(value) { return new Intl.DateTimeFormat("ko-KR", { year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" }).format(new Date(value)); }
function normalizeText(value) { return String(value || "").toLowerCase().replace(/\s+/g, ""); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch])); }
function escapeAttribute(value) { return escapeHtml(value); }
function showToast(message) { clearTimeout(toastTimer); els.toast.textContent = message; els.toast.classList.add("show"); toastTimer = setTimeout(() => els.toast.classList.remove("show"), 2200); }

init();
