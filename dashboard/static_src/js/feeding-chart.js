/* Baby Buddy Recent Feedings chart
 *
 * Points the card's readout at whichever day of the seven-day chart is being
 * hovered, tapped or focused. Every string is rendered server-side and carried
 * on the bar's data attributes, so nothing here needs formatting or
 * translating.
 *
 * The card is fully readable without this script: it arrives with today
 * selected and the whole chart drawn.
 */
(function () {
  function setUp(chart) {
    var readout = {
      total: chart.querySelector("[data-fr-total]"),
      count: chart.querySelector("[data-fr-count]"),
      when: chart.querySelector("[data-fr-when]"),
    };
    var bars = chart.querySelectorAll(".fr-bars .fr-hit");
    var rows = chart.querySelectorAll(".fr-row");
    var today = bars.length - 1;

    if (!bars.length || !readout.total) {
      return;
    }

    // The pinned day survives the pointer leaving; the previewed day lasts
    // only as long as the hover or focus that set it. Keeping them apart means
    // exactly one day can ever be shown as selected.
    var pinned = today;
    var preview = null;

    function show(index) {
      var bar = bars[index];
      readout.total.textContent = bar.dataset.total;
      readout.count.textContent = bar.dataset.count;
      readout.when.textContent = bar.dataset.when;
      readout.when.classList.toggle("is-selected", index !== today);

      for (var i = 0; i < bars.length; i++) {
        bars[i].setAttribute("aria-pressed", String(i === index));
      }
      // Counts, bars and day letters each have their own cell per day.
      for (var r = 0; r < rows.length; r++) {
        var cells = rows[r].querySelectorAll(".fr-cell");
        for (var c = 0; c < cells.length; c++) {
          cells[c].classList.toggle("is-active", c === index);
        }
      }
    }

    function render() {
      show(preview === null ? pinned : preview);
    }

    Array.prototype.forEach.call(bars, function (bar, index) {
      bar.addEventListener("mouseenter", function () {
        preview = index;
        render();
      });
      bar.addEventListener("focus", function () {
        preview = index;
        render();
      });
      bar.addEventListener("blur", function () {
        preview = null;
        render();
      });
      // A click always pins that day. Today's bar is the way back to today, so
      // clicking the day already selected does nothing at all.
      bar.addEventListener("click", function () {
        pinned = index;
        preview = index;
        render();
      });
      bar.addEventListener("keydown", function (event) {
        var step =
          event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (!step) {
          return;
        }
        event.preventDefault();
        bars[Math.min(bars.length - 1, Math.max(0, index + step))].focus();
      });
    });

    chart.querySelector(".fr-bars").addEventListener("mouseleave", function () {
      preview = null;
      render();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-feeding-chart]"),
      setUp,
    );
  });
})();
