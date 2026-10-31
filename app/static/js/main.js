/**
 * InternAI — Main Frontend Script
 * Handles CSRF-protected AJAX requests, dynamic toasts, modals, loading states,
 * and skill management interactions.
 */

document.addEventListener("DOMContentLoaded", () => {
  initMobileSidebar();
  initAutoDismissFlashes();
  initModalBackdrops();
});

// ============================================================================
// CSRF & HTTP Request Helpers
// ============================================================================

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute("content") : "";
}

async function apiRequest(url, options = {}) {
  const defaultHeaders = {
    "X-CSRFToken": getCsrfToken(),
    "Accept": "application/json",
  };

  if (!(options.body instanceof FormData)) {
    defaultHeaders["Content-Type"] = "application/json";
  }

  const config = {
    ...options,
    headers: {
      ...defaultHeaders,
      ...(options.headers || {}),
    },
  };

  try {
    const response = await fetch(url, config);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || data.message || `Request failed with status ${response.status}`);
    }
    return data;
  } catch (err) {
    console.error("API Request Error:", err);
    throw err;
  }
}

// ============================================================================
// Toast Notifications
// ============================================================================

function showToast(message, type = "info", duration = 4500) {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  
  // Icon based on type
  let iconSvg = "";
  if (type === "success") {
    iconSvg = `<span style="color: var(--success); font-weight: bold;">✓</span>`;
  } else if (type === "danger") {
    iconSvg = `<span style="color: var(--danger); font-weight: bold;">✕</span>`;
  } else if (type === "warning") {
    iconSvg = `<span style="color: var(--warning); font-weight: bold;">⚠</span>`;
  } else {
    iconSvg = `<span style="color: var(--info); font-weight: bold;">ℹ</span>`;
  }

  toast.innerHTML = `
    <div style="display:flex; align-items:center; gap:0.5rem; flex:1;">
      ${iconSvg}
      <div class="toast-message">${escapeHtml(message)}</div>
    </div>
    <button class="toast-close" aria-label="Close">&times;</button>
  `;

  const closeBtn = toast.querySelector(".toast-close");
  closeBtn.addEventListener("click", () => removeToast(toast));

  container.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => {
      removeToast(toast);
    }, duration);
  }
}

function removeToast(toast) {
  toast.style.opacity = "0";
  toast.style.transform = "translateX(100%)";
  setTimeout(() => {
    if (toast.parentElement) {
      toast.parentElement.removeChild(toast);
    }
  }, 300);
}

function initAutoDismissFlashes() {
  const flashData = document.querySelectorAll(".server-flash-message");
  flashData.forEach((el) => {
    const msg = el.getAttribute("data-message");
    const category = el.getAttribute("data-category") || "info";
    if (msg) {
      showToast(msg, category);
    }
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ============================================================================
// Modal Handlers
// ============================================================================

function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.add("open");
    document.body.style.overflow = "hidden";
  }
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.remove("open");
    document.body.style.overflow = "";
  }
}

function initModalBackdrops() {
  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) {
        closeModal(backdrop.id);
      }
    });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal-backdrop.open").forEach((modal) => {
        closeModal(modal.id);
      });
    }
  });
}

// ============================================================================
// Global Loading Spinner
// ============================================================================

function showSpinner(text = "Processing with AI...") {
  let overlay = document.getElementById("loading-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "loading-overlay";
    overlay.innerHTML = `
      <div class="spinner"></div>
      <div class="loading-text" id="loading-overlay-text">Processing...</div>
    `;
    document.body.appendChild(overlay);
  }
  const textEl = document.getElementById("loading-overlay-text");
  if (textEl) {
    textEl.textContent = text;
  }
  overlay.classList.add("active");
}

function hideSpinner() {
  const overlay = document.getElementById("loading-overlay");
  if (overlay) {
    overlay.classList.remove("active");
  }
}

// ============================================================================
// Mobile Sidebar Toggle
// ============================================================================

function initMobileSidebar() {
  const toggleBtn = document.getElementById("sidebar-toggle");
  const sidebar = document.querySelector(".app-sidebar");
  if (toggleBtn && sidebar) {
    toggleBtn.addEventListener("click", () => {
      sidebar.classList.toggle("mobile-open");
    });
  }
}

// ============================================================================
// Skills Search Autocomplete
// ============================================================================

let searchDebounceTimer = null;

function setupSkillSearch(inputSelector, resultsContainerSelector, onSelectCallback) {
  const input = document.querySelector(inputSelector);
  const container = document.querySelector(resultsContainerSelector);

  if (!input || !container) return;

  input.addEventListener("input", (e) => {
    clearTimeout(searchDebounceTimer);
    const query = e.target.value.trim();

    if (query.length === 0) {
      container.innerHTML = "";
      container.style.display = "none";
      return;
    }

    searchDebounceTimer = setTimeout(async () => {
      try {
        const skills = await apiRequest(`/profile/api/search-skills?q=${encodeURIComponent(query)}`, {
          method: "GET",
        });

        container.innerHTML = "";
        if (skills.length === 0) {
          container.innerHTML = `<div style="padding: 0.5rem; color: var(--text-muted); font-size: 0.85rem;">No exact match found. Will create new skill: <strong>${escapeHtml(query)}</strong></div>`;
          container.style.display = "block";
          return;
        }

        container.style.display = "block";
        skills.slice(0, 8).forEach((item) => {
          const itemEl = document.createElement("div");
          itemEl.style.padding = "0.5rem 0.75rem";
          itemEl.style.cursor = "pointer";
          itemEl.style.borderBottom = "1px solid var(--border-color)";
          itemEl.style.display = "flex";
          itemEl.style.justifyContent = "space-between";
          itemEl.style.alignItems = "center";
          itemEl.style.fontSize = "0.88rem";

          itemEl.innerHTML = `
            <strong>${escapeHtml(item.name)}</strong>
            <span class="badge badge-gray" style="text-transform: capitalize;">${escapeHtml(item.category)}</span>
          `;

          itemEl.addEventListener("mouseenter", () => {
            itemEl.style.backgroundColor = "var(--bg-subtle)";
          });
          itemEl.addEventListener("mouseleave", () => {
            itemEl.style.backgroundColor = "#ffffff";
          });

          itemEl.addEventListener("click", () => {
            input.value = item.name;
            container.innerHTML = "";
            container.style.display = "none";
            if (typeof onSelectCallback === "function") {
              onSelectCallback(item);
            }
          });

          container.appendChild(itemEl);
        });
      } catch (err) {
        console.error("Failed to query skills:", err);
      }
    }, 250);
  });

  // Hide on click outside
  document.addEventListener("click", (e) => {
    if (!input.contains(e.target) && !container.contains(e.target)) {
      container.style.display = "none";
    }
  });
}
