(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const route = location.pathname;
  for (const id of ["booking-form", "confirmation", "reservation-detail", "reservation-cancel-button"]) {
    $(id).removeAttribute("data-testid");
  }
  const sessionKey = "tablekeeper-session";
  const pendingKey = "tablekeeper-pending-booking";
  const routeParams = new URLSearchParams(location.search);
  let sessionViewChanged = () => {};
  function readStoredSession() {
    try {
      const value = JSON.parse(localStorage.getItem(sessionKey));
      return value && typeof value.token === "string" ? value : null;
    } catch (_) { return null; }
  }
  function sameSession(left, right) {
    return left?.token === right?.token && left?.user_id === right?.user_id
      && left?.display_name === right?.display_name;
  }
  let session = readStoredSession();
  function applySession(next, persist = false) {
    if (persist) {
      if (next) localStorage.setItem(sessionKey, JSON.stringify(next));
      else localStorage.removeItem(sessionKey);
    }
    const changed = !sameSession(session, next);
    session = next;
    showSession();
    if (changed) sessionViewChanged();
  }
  function reconcileSession() {
    applySession(readStoredSession());
  }
  window.addEventListener("storage", (event) => {
    if (event.storageArea === localStorage && (event.key === sessionKey || event.key === null)) reconcileSession();
  });
  window.addEventListener("pageshow", reconcileSession);

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function feedback(container, testid, text, kind) {
    container.replaceChildren();
    if (!text) return;
    const item = node("div", `message ${kind}`, text);
    item.dataset.testid = testid;
    container.append(item);
  }
  function showSession() {
    const signedIn = !!(session && session.token);
    $("current-user").hidden = !signedIn;
    $("logout-button").hidden = !signedIn;
    if (signedIn) {
      $("current-user").dataset.testid = "current-user";
      $("logout-button").dataset.testid = "logout-button";
    } else {
      $("current-user").removeAttribute("data-testid");
      $("logout-button").removeAttribute("data-testid");
    }
    $("login-link").hidden = signedIn;
    $("signup-link").hidden = signedIn;
    if (signedIn) $("current-user").textContent = session.display_name;
  }
  $("logout-button").addEventListener("click", () => {
    applySession(null, true);
    localStorage.removeItem(pendingKey);
    location.assign("/");
  });
  showSession();

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const stored = readStoredSession();
    if (!sameSession(session, stored)) applySession(stored);
    const requestToken = options.auth === false ? null : stored?.token;
    if (requestToken) headers.Authorization = `Bearer ${requestToken}`;
    const response = await fetch(path, {
      method: options.method || "GET", headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    let value = null;
    if (response.status !== 204) value = await response.json();
    if (!response.ok) {
      if (response.status === 401 && requestToken) {
        const current = readStoredSession();
        if (current?.token === requestToken) applySession(null, true);
        else if (!sameSession(session, current)) applySession(current);
      }
      const error = new Error(value?.error?.message || `Request refused (${response.status})`);
      error.status = response.status;
      error.code = value?.error?.code;
      throw error;
    }
    return value;
  }
  function friendly(error) {
    if (error.code === "table_unavailable") return "That table has just been taken. Please choose another option.";
    if (error.code === "cutoff_passed") return "This reservation is past its cancellation window.";
    if (error.status === 401) return "Please log in to continue.";
    return error.message || "Something went wrong. Please try again.";
  }
  function friendlyDate(value) {
    const [year, month, day] = value.slice(0, 10).split("-").map(Number);
    return new Intl.DateTimeFormat("en-US", {
      weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
    }).format(new Date(Date.UTC(year, month - 1, day, 12)));
  }
  function friendlyTime(value) {
    const raw = value.slice(11, 16);
    const [hour, minute] = raw.split(":").map(Number);
    const displayHour = hour % 12 || 12;
    return `${displayHour}:${String(minute).padStart(2, "0")} ${hour < 12 ? "AM" : "PM"}`;
  }
  if (route === "/") {
    $("home-screen").hidden = false;
    initHome();
  } else if (route === "/signup" || route === "/login") {
    $(`${route.slice(1)}-screen`).hidden = false;
    initAuth(route.slice(1));
  } else if (route === "/lookup") {
    $("lookup-screen").hidden = false;
    initLookup();
  } else {
    $("not-found-screen").hidden = false;
  }

  function initAuth(kind) {
    const form = $(`${kind}-form`);
    const box = $(`${kind}-feedback`);
    const returnIntent = routeParams.get("return");
    const safeReturn = returnIntent === "booking" || returnIntent === "lookup" ? returnIntent : null;
    const other = kind === "login" ? $("login-signup-link") : $("signup-login-link");
    if (safeReturn) other.href = `/${kind === "login" ? "signup" : "login"}?return=${safeReturn}`;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      feedback(box, "auth-error", "", "error");
      const fields = new FormData(form);
      const body = { email: fields.get("email"), password: fields.get("password") };
      if (kind === "signup") body.display_name = fields.get("display_name");
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        const result = await api(`/auth/${kind}`, { method: "POST", body, auth: false });
        applySession({ token: result.token, display_name: result.display_name, user_id: result.user_id }, true);
        if (safeReturn === "booking") location.assign("/?resume=booking");
        else if (safeReturn === "lookup") location.assign("/lookup");
        else location.assign("/");
      } catch (error) {
        feedback(box, "auth-error", friendly(error), "error");
      } finally { button.disabled = false; }
    });
  }

  function initHome() {
    const select = $("restaurant-select");
    const date = $("date-input");
    date.value = new Date().toLocaleDateString("en-CA");
    let searchGeneration = 0;
    let formGeneration = 0;
    let activeQuery = null;
    let activeRestaurant = null;
    let selected = null;
    let frozenAttempt = null;
    let bookingBusy = false;
    let restaurants = [];
    let pendingResume = null;

    const bookingBox = $("booking-form");
    const confirmation = $("confirmation");
    const bookingFeedback = $("booking-feedback");
    const grid = $("availability-grid");

    function readPendingResume() {
      if (!session?.token || routeParams.get("resume") !== "booking") return null;
      try {
        const value = JSON.parse(localStorage.getItem(pendingKey));
        const valid = value && typeof value.restaurantId === "string" && typeof value.date === "string"
          && Number.isInteger(value.size) && value.size > 0 && typeof value.local === "string"
          && Array.isArray(value.ids) && value.ids.length > 0 && value.ids.every((id) => typeof id === "string")
          && Number.isFinite(value.createdAt) && Date.now() - value.createdAt < 30 * 60 * 1000;
        if (!valid) throw new Error("invalid pending booking");
        return value;
      } catch (_) {
        localStorage.removeItem(pendingKey);
        feedback($("home-notice"), "pending-booking-notice", "That saved table could not be restored. Please search again.", "info");
        return null;
      }
    }
    pendingResume = readPendingResume();

    function retireSelection() {
      formGeneration++;
      selected = null;
      frozenAttempt = null;
      bookingBusy = false;
      $("booking-submit").disabled = false;
      bookingBox.hidden = true;
      bookingBox.removeAttribute("data-testid");
      confirmation.hidden = true;
      confirmation.removeAttribute("data-testid");
      bookingFeedback.replaceChildren();
    }
    function tableLabels(ids, restaurant) {
      const map = new Map((restaurant?.tables || []).map((table) => [table.id, table.label]));
      return ids.map((id) => map.get(id) || id);
    }
    function renderGrid(availability, restaurant, query) {
      grid.replaceChildren();
      const slots = availability.slots || [];
      grid.hidden = slots.length === 0;
      if (slots.length) grid.dataset.testid = "availability-grid";
      else grid.removeAttribute("data-testid");
      $("no-slots").hidden = slots.length !== 0;
      $("search-status").hidden = true;
      $("results-context").textContent = `${restaurant.name} · ${friendlyDate(query.date)} · ${query.size} ${query.size === 1 ? "guest" : "guests"}`;
      $("results-guidance").textContent = "Choose a start time and seating option.";
      function finishPendingResume() {
        if (!pendingResume) return;
        const pending = pendingResume;
        pendingResume = null;
        localStorage.removeItem(pendingKey);
        const time = pending.local.slice(11, 16);
        const testid = `slot-${pending.ids.join("+")}-${time}`;
        const choice = [...grid.querySelectorAll("button[data-testid]")].find((item) => item.dataset.testid === testid && !item.disabled);
        if (query.restaurantId === pending.restaurantId && query.date === pending.date && query.size === pending.size && choice) {
          choice.click();
          feedback($("home-notice"), "pending-booking-notice", "Your table is still available. Review it below to confirm.", "info");
        } else {
          feedback($("home-notice"), "pending-booking-notice", "That table is no longer available. Your search is restored—please choose another option.", "info");
        }
      }
      if (!slots.length) {
        finishPendingResume();
        return;
      }
      for (const slot of slots) {
        const time = slot.starts_at_local.slice(11, 16);
        const card = node("article", "time-card");
        const heading = node("div", "time-label", friendlyTime(slot.starts_at_local));
        heading.append(node("span", "time-caption", `${time} local`));
        card.append(heading);
        const options = node("div", "options");
        const availableSingles = new Set(slot.available_table_ids || []);
        const availableSingleOptions = new Map((slot.available_options || []).filter((option) => option.table_ids.length === 1).map((option) => [option.table_ids[0], option]));
        const availablePairs = new Map((slot.available_options || []).filter((option) => option.table_ids.length === 2).map((option) => [option.table_ids.join("+"), option]));
        for (const table of restaurant.tables) {
          const available = availableSingles.has(table.id);
          const choice = node("button", "slot", table.label);
          choice.type = "button";
          choice.setAttribute("aria-pressed", "false");
          choice.dataset.testid = `slot-${table.id}-${time}`;
          choice.dataset.available = String(available);
          choice.disabled = !available;
          const currentOption = availableSingleOptions.get(table.id);
          choice.append(node("small", "", available ? (currentOption ? `Seats ${currentOption.capacity}` : "Available") : "Unavailable"));
          if (available) choice.addEventListener("click", () => choose([table.id], slot, restaurant, query, choice));
          options.append(choice);
        }
        for (const pair of restaurant.combinable || []) {
          const option = availablePairs.get(pair.join("+"));
          if (!option) continue;
          const labels = tableLabels(pair, restaurant);
          const choice = node("button", "slot pair", labels.join(" + "));
          choice.type = "button";
          choice.setAttribute("aria-pressed", "false");
          choice.dataset.testid = `slot-${pair.join("+")}-${time}`;
          choice.dataset.available = "true";
          choice.append(node("small", "", `Together · seats ${option.capacity}`));
          choice.addEventListener("click", () => choose(pair, slot, restaurant, query, choice));
          options.append(choice);
        }
        card.append(options);
        grid.append(card);
      }
      finishPendingResume();
    }
    function choose(ids, slot, restaurant, query, choice) {
      if (!session || !session.token) {
        localStorage.setItem(pendingKey, JSON.stringify({
          restaurantId: restaurant.id, date: query.date, size: query.size,
          local: slot.starts_at_local, ids: [...ids], createdAt: Date.now(),
        }));
        location.assign("/login?return=booking");
        return;
      }
      formGeneration++;
      frozenAttempt = null;
      bookingBusy = false;
      $("booking-submit").disabled = false;
      selected = {
        ids: [...ids], restaurantId: restaurant.id, restaurantName: restaurant.name,
        labels: tableLabels(ids, restaurant), local: slot.starts_at_local, partySize: query.size,
      };
      grid.querySelectorAll(".selected").forEach((item) => {
        item.classList.remove("selected");
        item.setAttribute("aria-pressed", "false");
      });
      choice.classList.add("selected");
      choice.setAttribute("aria-pressed", "true");
      $("booking-party-size").value = String(query.size);
      const rawTime = selected.local.slice(11, 16);
      $("booking-summary").textContent = `${selected.labels.join(" + ")} · ${friendlyDate(selected.local)} at ${friendlyTime(selected.local)} (${rawTime} local) · ${restaurant.name}`;
      bookingBox.hidden = false;
      bookingBox.dataset.testid = "booking-form";
      confirmation.hidden = true;
      confirmation.removeAttribute("data-testid");
      bookingFeedback.replaceChildren();
      bookingBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
      $("booking-party-size").focus({ preventScroll: true });
    }
    async function search(event) {
      event.preventDefault();
      const generation = ++searchGeneration;
      retireSelection();
      const query = { restaurantId: select.value, date: date.value, size: Number($("party-size-input").value) };
      activeQuery = query;
      activeRestaurant = null;
      grid.hidden = true;
      grid.removeAttribute("data-testid");
      grid.replaceChildren();
      $("no-slots").hidden = true;
      $("search-status").hidden = false;
      $("search-status").textContent = "Looking for the right table…";
      try {
        const params = new URLSearchParams({ restaurant_id: query.restaurantId, date: query.date, party_size: String(query.size) });
        const [restaurant, availability] = await Promise.all([
          api(`/restaurants/${encodeURIComponent(query.restaurantId)}`),
          api(`/availability?${params}`),
        ]);
        if (generation !== searchGeneration) return;
        activeRestaurant = restaurant;
        renderGrid(availability, restaurant, query);
      } catch (error) {
        if (generation !== searchGeneration) return;
        $("search-status").hidden = false;
        $("search-status").textContent = `We couldn't load tables. ${friendly(error)}`;
      }
    }
    $("search-form").addEventListener("submit", search);
    $("party-size-input").addEventListener("input", () => {
      if (!activeQuery || Number($("party-size-input").value) === activeQuery.size) return;
      ++searchGeneration;
      retireSelection();
      activeQuery = null;
      activeRestaurant = null;
      grid.hidden = true;
      grid.removeAttribute("data-testid");
      grid.replaceChildren();
      $("no-slots").hidden = true;
      $("search-status").hidden = false;
      $("search-status").textContent = "Guest count changed. Search again to see tables that fit.";
    });
    $("search-button").disabled = true;
    api("/restaurants").then((result) => {
      restaurants = result.restaurants || [];
      select.replaceChildren();
      if (!restaurants.length) {
        select.append(node("option", "", "No restaurants yet"));
        $("search-status").textContent = "There are no restaurants to show right now.";
        return;
      }
      for (const restaurant of restaurants) {
        const option = node("option", "", restaurant.name);
        option.value = restaurant.id;
        select.append(option);
      }
      $("search-button").disabled = false;
      if (pendingResume) {
        if (!restaurants.some((restaurant) => restaurant.id === pendingResume.restaurantId)) {
          pendingResume = null;
          localStorage.removeItem(pendingKey);
          feedback($("home-notice"), "pending-booking-notice", "That restaurant is no longer available. Please start a new search.", "info");
          return;
        }
        select.value = pendingResume.restaurantId;
        date.value = pendingResume.date;
        $("party-size-input").value = String(pendingResume.size);
        search({ preventDefault() {} });
      }
    }).catch((error) => {
      $("search-status").textContent = `We couldn't load restaurants. ${friendly(error)}`;
    });

    async function refreshAfterConflict(generation) {
      if (generation !== searchGeneration || !activeQuery || !activeRestaurant) return;
      const query = activeQuery;
      const params = new URLSearchParams({ restaurant_id: query.restaurantId, date: query.date, party_size: String(query.size) });
      try {
        const availability = await api(`/availability?${params}`);
        if (generation === searchGeneration) renderGrid(availability, activeRestaurant, query);
      } catch (_) { /* preserve form and the confirmed refusal */ }
    }

    function exactOptionIsAvailable(availability, selection) {
      const slot = (availability.slots || []).find((item) => item.starts_at_local === selection.local);
      if (!slot) return false;
      if (selection.ids.length === 1) return (slot.available_table_ids || []).includes(selection.ids[0]);
      return (slot.available_options || []).some((option) =>
        option.table_ids.length === selection.ids.length && selection.ids.every((id) => option.table_ids.includes(id)));
    }

    $("booking-form-inner").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!selected || bookingBusy) return;
      const requestedSize = Number($("booking-party-size").value);
      const currentForm = formGeneration;
      const currentSearch = searchGeneration;
      bookingBusy = true;
      $("booking-submit").disabled = true;
      bookingFeedback.replaceChildren();
      confirmation.hidden = true;
      confirmation.removeAttribute("data-testid");
      let submitted = false;
      try {
        if (requestedSize !== selected.partySize) {
          const query = {
            restaurantId: selected.restaurantId,
            date: selected.local.slice(0, 10),
            size: requestedSize,
          };
          const params = new URLSearchParams({
            restaurant_id: query.restaurantId,
            date: query.date,
            party_size: String(query.size),
          });
          feedback(bookingFeedback, "booking-recheck", "Guest count changed. Rechecking this table before booking…", "info");
          const availability = await api(`/availability?${params}`);
          if (currentForm !== formGeneration) return;
          const restaurant = activeRestaurant?.id === selected.restaurantId
            ? activeRestaurant
            : await api(`/restaurants/${encodeURIComponent(selected.restaurantId)}`);
          activeQuery = query;
          activeRestaurant = restaurant;
          $("party-size-input").value = String(requestedSize);
          frozenAttempt = null;
          if (!exactOptionIsAvailable(availability, selected)) {
            retireSelection();
            renderGrid(availability, restaurant, query);
            feedback($("home-notice"), "booking-error", "That table is not available for the updated guest count. Please choose another option.", "error");
            return;
          }
          selected.partySize = requestedSize;
          renderGrid(availability, restaurant, query);
          const time = selected.local.slice(11, 16);
          const testid = `slot-${selected.ids.join("+")}-${time}`;
          const refreshedChoice = [...grid.querySelectorAll("button[data-testid]")].find((item) => item.dataset.testid === testid);
          if (refreshedChoice) {
            refreshedChoice.classList.add("selected");
            refreshedChoice.setAttribute("aria-pressed", "true");
          }
          bookingFeedback.replaceChildren();
        }
        const body = {
          restaurant_id: selected.restaurantId,
          starts_at_local: selected.local,
          party_size: selected.partySize,
        };
        if (selected.ids.length === 1) body.table_id = selected.ids[0];
        else body.table_ids = [...selected.ids];
        const signature = JSON.stringify(body);
        if (!frozenAttempt || frozenAttempt.signature !== signature) {
          frozenAttempt = { signature, body, key: crypto.randomUUID() };
        }
        const attempt = frozenAttempt;
        submitted = true;
        const result = await api("/reservations", { method: "POST", body: attempt.body, headers: { "Idempotency-Key": attempt.key } });
        if (currentForm !== formGeneration) return;
        $("confirmation-reference").textContent = result.reference;
        const rawTime = selected.local.slice(11, 16);
        $("confirmation-details").textContent = `${selected.restaurantName} · ${friendlyDate(selected.local)} at ${friendlyTime(selected.local)} (${rawTime} local) · ${selected.labels.join(" + ")}`;
        $("confirmation-tables").textContent = selected.labels.join(" + ");
        $("confirmation-view-reservation").href = `/lookup?reference=${encodeURIComponent(result.reference)}`;
        confirmation.hidden = false;
        confirmation.dataset.testid = "confirmation";
        localStorage.removeItem(pendingKey);
      } catch (error) {
        if (currentForm !== formGeneration) return;
        if (error.status) {
          feedback(bookingFeedback, "booking-error", friendly(error), "error");
          if (error.code === "table_unavailable") refreshAfterConflict(currentSearch);
        } else if (submitted) {
          feedback(bookingFeedback, "booking-uncertain", "We couldn't confirm the response. Your booking may have gone through. Retry this unchanged form to recover it.", "uncertain");
        } else {
          feedback(bookingFeedback, "booking-error", `We couldn't recheck this table. ${friendly(error)}`, "error");
        }
      } finally {
        if (currentForm === formGeneration) {
          bookingBusy = false;
          $("booking-submit").disabled = false;
        }
      }
    });
  }

  function initLookup() {
    const form = $("lookup-form");
    const box = $("lookup-feedback");
    const detail = $("reservation-detail");
    const list = $("reservation-list");
    const listStatus = $("reservation-list-status");
    let lookupGeneration = 0;
    let listGeneration = 0;
    let currentReference = "";
    let cancelBusy = false;
    const restaurantCache = new Map();

    async function restaurantFor(id) {
      if (!restaurantCache.has(id)) restaurantCache.set(id, api(`/restaurants/${encodeURIComponent(id)}`));
      return restaurantCache.get(id);
    }

    function hideDetail() {
      detail.hidden = true;
      detail.removeAttribute("data-testid");
      currentReference = "";
    }

    async function renderReservation(reservation, generation) {
      const restaurant = await restaurantFor(reservation.restaurant_id);
      if (generation !== lookupGeneration) return;
      const ids = reservation.table_ids || [reservation.table_id];
      const tableMap = new Map(restaurant.tables.map((table) => [table.id, table.label]));
      const labels = ids.map((id) => tableMap.get(id) || id);
      currentReference = reservation.reference;
      cancelBusy = false;
      $("lookup-reference-input").value = reservation.reference;
      list.querySelectorAll(".reservation-card.active").forEach((card) => card.classList.remove("active"));
      const activeCard = [...list.querySelectorAll(".reservation-card")].find((card) => card.dataset.reference === reservation.reference);
      if (activeCard) activeCard.classList.add("active");
      $("detail-heading").textContent = restaurant.name;
      $("detail-time").textContent = `${friendlyDate(reservation.starts_at_local)} at ${friendlyTime(reservation.starts_at_local)} · ${reservation.party_size} ${reservation.party_size === 1 ? "guest" : "guests"}`;
      $("reservation-tables").textContent = `${labels.length === 1 ? "Table" : "Tables"}: ${labels.join(" + ")}`;
      $("detail-reference").textContent = `Reference ${reservation.reference}`;
      $("reservation-status").textContent = reservation.status;
      $("reservation-status").classList.toggle("cancelled", reservation.status === "cancelled");
      $("reservation-cancel-button").hidden = reservation.status === "cancelled";
      $("reservation-cancel-button").disabled = false;
      if (reservation.status === "cancelled") $("reservation-cancel-button").removeAttribute("data-testid");
      else $("reservation-cancel-button").dataset.testid = "reservation-cancel-button";
      detail.hidden = false;
      detail.dataset.testid = "reservation-detail";
      detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    async function openReference(reference) {
      const generation = ++lookupGeneration;
      hideDetail();
      feedback(box, "reservation-error", "", "error");
      try {
        const reservation = await api(`/reservations/${encodeURIComponent(reference)}`);
        if (generation === lookupGeneration) await renderReservation(reservation, generation);
      } catch (error) {
        if (generation === lookupGeneration) feedback(box, "reservation-error", friendly(error), "error");
      }
    }

    function reservationSort(a, b) {
      return a.starts_at.localeCompare(b.starts_at) || a.reference.localeCompare(b.reference);
    }

    function appendGroup(title, reservations, restaurants) {
      if (!reservations.length) return;
      const group = node("section", "reservation-group");
      group.append(node("h2", "reservation-group-title", title));
      const cards = node("div", "reservation-cards");
      for (const reservation of reservations) {
        const restaurant = restaurants.get(reservation.restaurant_id);
        const card = node("button", "reservation-card");
        card.type = "button";
        card.dataset.testid = `reservation-card-${reservation.reference}`;
        card.dataset.reference = reservation.reference;
        card.classList.toggle("active", currentReference === reservation.reference);
        card.classList.toggle("cancelled", reservation.status === "cancelled");
        const ids = reservation.table_ids || [reservation.table_id];
        const tableMap = new Map((restaurant?.tables || []).map((table) => [table.id, table.label]));
        const labels = ids.map((id) => tableMap.get(id) || id);
        card.append(node("strong", "reservation-card-name", restaurant?.name || reservation.restaurant_id));
        card.append(node("span", "reservation-card-date", friendlyDate(reservation.starts_at_local)));
        card.append(node("span", "reservation-card-time", `${friendlyTime(reservation.starts_at_local)} · ${labels.join(" + ")}`));
        card.append(node("span", "reservation-card-meta", `${reservation.party_size} ${reservation.party_size === 1 ? "guest" : "guests"}`));
        card.append(node("span", "reservation-card-reference", `Ref ${reservation.reference}`));
        card.append(node("span", `status-pill ${reservation.status === "cancelled" ? "cancelled" : ""}`, reservation.status));
        card.addEventListener("click", () => openReference(reservation.reference));
        cards.append(card);
      }
      group.append(cards);
      list.append(group);
    }

    async function loadReservations() {
      const generation = ++listGeneration;
      list.replaceChildren();
      listStatus.hidden = false;
      listStatus.textContent = "Loading your reservations…";
      try {
        const result = await api("/reservations");
        const reservations = result.reservations || [];
        const restaurants = new Map();
        await Promise.all([...new Set(reservations.map((item) => item.restaurant_id))].map(async (id) => {
          restaurants.set(id, await restaurantFor(id));
        }));
        if (generation !== listGeneration) return;
        const now = Date.now();
        const upcoming = reservations.filter((item) => item.status !== "cancelled" && Date.parse(item.ends_at) >= now).sort(reservationSort);
        const past = reservations.filter((item) => item.status !== "cancelled" && Date.parse(item.ends_at) < now).sort((a, b) => reservationSort(b, a));
        const cancelled = reservations.filter((item) => item.status === "cancelled").sort((a, b) => reservationSort(b, a));
        list.replaceChildren();
        appendGroup("Upcoming", upcoming, restaurants);
        appendGroup("Past", past, restaurants);
        appendGroup("Cancelled", cancelled, restaurants);
        listStatus.textContent = reservations.length ? `${reservations.length} ${reservations.length === 1 ? "reservation" : "reservations"}` : "No reservations yet. When you book a table, it will appear here automatically.";
      } catch (error) {
        if (generation === listGeneration) listStatus.textContent = `We couldn't load your reservations. ${friendly(error)}`;
      }
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      openReference($("lookup-reference-input").value.trim());
    });
    $("reservation-cancel-button").addEventListener("click", async () => {
      const reference = currentReference;
      if (!reference || cancelBusy) return;
      cancelBusy = true;
      $("reservation-cancel-button").disabled = true;
      const generation = lookupGeneration;
      feedback(box, "reservation-error", "", "error");
      try {
        const reservation = await api(`/reservations/${encodeURIComponent(reference)}/cancel`, { method: "POST" });
        if (generation === lookupGeneration) {
          await renderReservation(reservation, generation);
          await loadReservations();
        }
      } catch (error) {
        if (generation === lookupGeneration) {
          cancelBusy = false;
          $("reservation-cancel-button").disabled = false;
          feedback(box, "reservation-error", friendly(error), "error");
        }
      }
    });

    const deepLinkReference = routeParams.get("reference");
    function reconcileLookupSession() {
      lookupGeneration++;
      listGeneration++;
      hideDetail();
      list.replaceChildren();
      $("lookup-reference-input").value = "";
      feedback(box, "reservation-error", "", "error");
      if (!session?.token) {
        $("lookup-auth-gate").hidden = false;
        $("lookup-account").hidden = true;
        listStatus.textContent = "Log in to view your reservations.";
        return;
      }
      $("lookup-auth-gate").hidden = true;
      $("lookup-account").hidden = false;
      loadReservations();
      if (deepLinkReference) {
        $("lookup-reference-input").value = deepLinkReference;
        openReference(deepLinkReference);
      }
    }
    sessionViewChanged = reconcileLookupSession;
    reconcileLookupSession();
  }
})();
