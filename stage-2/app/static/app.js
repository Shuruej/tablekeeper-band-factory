(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const route = location.pathname;
  for (const id of ["booking-form", "confirmation", "reservation-detail", "reservation-cancel-button"]) {
    $(id).removeAttribute("data-testid");
  }
  const sessionKey = "tablekeeper-session";
  let session = null;
  try { session = JSON.parse(localStorage.getItem(sessionKey)); } catch (_) { /* no session */ }

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
    session = null;
    localStorage.removeItem(sessionKey);
    showSession();
    location.assign("/");
  });
  showSession();

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (session && session.token) headers.Authorization = `Bearer ${session.token}`;
    const response = await fetch(path, {
      method: options.method || "GET", headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    let value = null;
    if (response.status !== 204) value = await response.json();
    if (!response.ok) {
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

  if (route === "/" || !["/signup", "/login", "/lookup"].includes(route)) {
    $("home-screen").hidden = false;
    initHome();
  } else if (route === "/signup" || route === "/login") {
    $(`${route.slice(1)}-screen`).hidden = false;
    initAuth(route.slice(1));
  } else {
    $("lookup-screen").hidden = false;
    initLookup();
  }

  function initAuth(kind) {
    const form = $(`${kind}-form`);
    const box = $(`${kind}-feedback`);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      feedback(box, "auth-error", "", "error");
      const fields = new FormData(form);
      const body = { email: fields.get("email"), password: fields.get("password") };
      if (kind === "signup") body.display_name = fields.get("display_name");
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        const result = await api(`/auth/${kind}`, { method: "POST", body });
        session = { token: result.token, display_name: result.display_name, user_id: result.user_id };
        localStorage.setItem(sessionKey, JSON.stringify(session));
        showSession();
        location.assign("/");
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

    const bookingBox = $("booking-form");
    const confirmation = $("confirmation");
    const bookingFeedback = $("booking-feedback");
    const grid = $("availability-grid");

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
      $("results-context").textContent = `${restaurant.name} · ${query.date} · ${query.size} ${query.size === 1 ? "guest" : "guests"}`;
      if (!slots.length) return;
      for (const slot of slots) {
        const time = slot.starts_at_local.slice(11, 16);
        const card = node("article", "time-card");
        const heading = node("div", "time-label", time);
        heading.append(node("span", "time-caption", "Local time"));
        card.append(heading);
        const options = node("div", "options");
        const availableSingles = new Set(slot.available_table_ids || []);
        const availablePairs = new Map((slot.available_options || []).filter((option) => option.table_ids.length === 2).map((option) => [option.table_ids.join("+"), option]));
        for (const table of restaurant.tables) {
          const available = availableSingles.has(table.id);
          const choice = node("button", "slot", table.label);
          choice.type = "button";
          choice.dataset.testid = `slot-${table.id}-${time}`;
          choice.dataset.available = String(available);
          choice.disabled = !available;
          choice.append(node("small", "", available ? `Seats ${table.capacity}` : "Unavailable"));
          if (available) choice.addEventListener("click", () => choose([table.id], slot, restaurant, query, choice));
          options.append(choice);
        }
        for (const pair of restaurant.combinable || []) {
          const option = availablePairs.get(pair.join("+"));
          if (!option) continue;
          const labels = tableLabels(pair, restaurant);
          const choice = node("button", "slot pair", labels.join(" + "));
          choice.type = "button";
          choice.dataset.testid = `slot-${pair.join("+")}-${time}`;
          choice.dataset.available = "true";
          choice.append(node("small", "", `Together · seats ${option.capacity}`));
          choice.addEventListener("click", () => choose(pair, slot, restaurant, query, choice));
          options.append(choice);
        }
        card.append(options);
        grid.append(card);
      }
    }
    function choose(ids, slot, restaurant, query, choice) {
      if (!session || !session.token) { location.assign("/login"); return; }
      formGeneration++;
      frozenAttempt = null;
      bookingBusy = false;
      $("booking-submit").disabled = false;
      selected = {
        ids: [...ids], restaurantId: restaurant.id, restaurantName: restaurant.name,
        labels: tableLabels(ids, restaurant), local: slot.starts_at_local,
      };
      grid.querySelectorAll(".selected").forEach((item) => item.classList.remove("selected"));
      choice.classList.add("selected");
      $("booking-party-size").value = String(query.size);
      $("booking-summary").textContent = `${selected.labels.join(" + ")} at ${selected.local.replace("T", " ")} · ${restaurant.name}`;
      bookingBox.hidden = false;
      bookingBox.dataset.testid = "booking-form";
      confirmation.hidden = true;
      confirmation.removeAttribute("data-testid");
      bookingFeedback.replaceChildren();
      bookingBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    $("booking-party-size").addEventListener("input", () => {
      formGeneration++;
      frozenAttempt = null;
      bookingBusy = false;
      $("booking-submit").disabled = false;
      confirmation.hidden = true;
      confirmation.removeAttribute("data-testid");
      bookingFeedback.replaceChildren();
    });

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
    $("booking-form-inner").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!selected || bookingBusy) return;
      const body = {
        restaurant_id: selected.restaurantId,
        starts_at_local: selected.local,
        party_size: Number($("booking-party-size").value),
      };
      if (selected.ids.length === 1) body.table_id = selected.ids[0];
      else body.table_ids = [...selected.ids];
      const signature = JSON.stringify(body);
      if (!frozenAttempt || frozenAttempt.signature !== signature) {
        frozenAttempt = { signature, body, key: crypto.randomUUID() };
      }
      const attempt = frozenAttempt;
      const currentForm = formGeneration;
      const currentSearch = searchGeneration;
      bookingBusy = true;
      $("booking-submit").disabled = true;
      bookingFeedback.replaceChildren();
      confirmation.hidden = true;
      confirmation.removeAttribute("data-testid");
      try {
        const result = await api("/reservations", { method: "POST", body: attempt.body, headers: { "Idempotency-Key": attempt.key } });
        if (currentForm !== formGeneration) return;
        $("confirmation-reference").textContent = result.reference;
        $("confirmation-details").textContent = `${selected.restaurantName} · ${selected.labels.join(" + ")} · ${selected.local.replace("T", " ")}`;
        $("confirmation-tables").textContent = selected.labels.join(" + ");
        confirmation.hidden = false;
        confirmation.dataset.testid = "confirmation";
      } catch (error) {
        if (currentForm !== formGeneration) return;
        if (error.status) {
          feedback(bookingFeedback, "booking-error", friendly(error), "error");
          if (error.code === "table_unavailable") refreshAfterConflict(currentSearch);
        } else {
          feedback(bookingFeedback, "booking-uncertain", "We couldn't confirm the response. Your booking may have gone through. Retry this unchanged form to recover it.", "uncertain");
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
    let lookupGeneration = 0;
    async function renderReservation(reservation, generation) {
      const restaurant = await api(`/restaurants/${encodeURIComponent(reservation.restaurant_id)}`);
      if (generation !== lookupGeneration) return;
      const ids = reservation.table_ids || [reservation.table_id];
      const tableMap = new Map(restaurant.tables.map((table) => [table.id, table.label]));
      const labels = ids.map((id) => tableMap.get(id) || id);
      $("detail-reference").textContent = reservation.reference;
      $("detail-time").textContent = `${restaurant.name} · ${reservation.starts_at_local.replace("T", " ")} · ${reservation.party_size} ${reservation.party_size === 1 ? "guest" : "guests"}`;
      $("reservation-tables").textContent = labels.join(" + ");
      $("reservation-status").textContent = reservation.status;
      $("reservation-status").classList.toggle("cancelled", reservation.status === "cancelled");
      $("reservation-cancel-button").hidden = reservation.status === "cancelled";
      if (reservation.status === "cancelled") $("reservation-cancel-button").removeAttribute("data-testid");
      else $("reservation-cancel-button").dataset.testid = "reservation-cancel-button";
      detail.hidden = false;
      detail.dataset.testid = "reservation-detail";
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const generation = ++lookupGeneration;
      detail.hidden = true;
      detail.removeAttribute("data-testid");
      feedback(box, "reservation-error", "", "error");
      const reference = $("lookup-reference-input").value.trim();
      try {
        if (!session?.token) throw Object.assign(new Error("Please log in to view your reservation."), { status: 401 });
        const reservation = await api(`/reservations/${encodeURIComponent(reference)}`);
        if (generation === lookupGeneration) await renderReservation(reservation, generation);
      } catch (error) {
        if (generation === lookupGeneration) feedback(box, "reservation-error", friendly(error), "error");
      }
    });
    $("reservation-cancel-button").addEventListener("click", async () => {
      const generation = lookupGeneration;
      feedback(box, "reservation-error", "", "error");
      try {
        const reference = $("lookup-reference-input").value.trim();
        const reservation = await api(`/reservations/${encodeURIComponent(reference)}/cancel`, { method: "POST" });
        if (generation === lookupGeneration) await renderReservation(reservation, generation);
      } catch (error) {
        if (generation === lookupGeneration) feedback(box, "reservation-error", friendly(error), "error");
      }
    });
  }
})();
