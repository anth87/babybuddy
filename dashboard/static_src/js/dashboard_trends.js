/* Baby Saliba Dashboard Trends
 *
 * Drives the "Past 7 days" panel: switching between activities and picking a
 * day to read. The panel is rendered server-side for the first activity, so
 * this only ever swaps values that are already on the page.
 */
BabyBuddy.DashboardTrends = (function () {
  var panel = null;
  var metrics = {};
  var current = null;
  var picked = null; // Day locked in by a click.
  var hovered = null; // Day under the pointer.

  function activeIndex() {
    if (hovered !== null) {
      return hovered;
    }
    return picked === null ? 6 : picked;
  }

  function text(selector, value) {
    var element = panel.querySelector(selector);
    if (element) {
      element.textContent = value;
    }
  }

  function render() {
    var metric = metrics[current];
    if (!metric) {
      return;
    }
    var day = metric.days[activeIndex()];

    panel.className = panel.className.replace(
      /babyb-act--\S+/,
      "babyb-act--" + current,
    );
    panel.setAttribute("data-metric", current);

    text("[data-babyb-headline]", day.headline);
    text("[data-babyb-subline]", day.subline);
    text("[data-babyb-day]", day.name);
    text("[data-babyb-average-label]", metric.average_label);
    text("[data-babyb-interval]", metric.interval || "—");
    text("[data-babyb-total]", metric.total);

    var average = panel.querySelector("[data-babyb-average]");
    if (average) {
      average.style.bottom = metric.average_pct + "%";
    }

    var legend = panel.querySelector("[data-babyb-legend]");
    if (legend) {
      legend.classList.toggle("d-none", !metric.split);
    }

    panel.querySelectorAll("[data-babyb-bar]").forEach(function (bar) {
      var index = parseInt(bar.getAttribute("data-babyb-bar"), 10);
      var value = metric.days[index];
      bar.querySelector(".babyb-bar-value").textContent = value.display;
      bar.querySelector(".babyb-bar-track").style.height = value.height + "%";
      bar.querySelector(".babyb-bar-lower").style.height = value.lower + "%";
      bar.classList.toggle("is-active", index === day.index);
      bar.setAttribute("aria-label", value.name);
    });

    panel.querySelectorAll("[data-babyb-day-label]").forEach(function (label) {
      var index = parseInt(label.getAttribute("data-babyb-day-label"), 10);
      label.classList.toggle("is-active", index === day.index);
    });

    panel.querySelectorAll("[data-babyb-metric]").forEach(function (tab) {
      var on = tab.getAttribute("data-babyb-metric") === current;
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });
  }

  function bind() {
    panel.querySelectorAll("[data-babyb-metric]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        current = tab.getAttribute("data-babyb-metric");
        // A day picked for one activity means nothing for the next.
        picked = null;
        hovered = null;
        render();
      });
    });

    panel.querySelectorAll("[data-babyb-bar]").forEach(function (bar) {
      var index = parseInt(bar.getAttribute("data-babyb-bar"), 10);
      bar.addEventListener("mouseenter", function () {
        hovered = index;
        render();
      });
      bar.addEventListener("mouseleave", function () {
        hovered = null;
        render();
      });
      bar.addEventListener("click", function () {
        picked = picked === index ? null : index;
        hovered = null;
        render();
      });
    });
  }

  return {
    init: function () {
      panel = document.querySelector("[data-babyb-trends]");
      var payload = document.getElementById("babyb-trend-data");
      if (!panel || !payload) {
        return;
      }
      JSON.parse(payload.textContent).forEach(function (metric) {
        metrics[metric.key] = metric;
      });
      current = panel.getAttribute("data-metric");
      bind();
      render();
    },
  };
})();
